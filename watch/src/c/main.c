#include <pebble.h>

#define MAX_TWEETS 15
#define AUTHOR_LEN 24
#define TEXT_LEN 441   // UTF-8 bytes; pkjs truncates to fit
#define TIME_LEN 8

// Commands (watch -> phone)
#define CMD_FETCH 0
#define CMD_REFRESH 1

// Feeds
#define FEED_FOLLOWING 0
#define FEED_FORYOU 1

// Status codes (phone -> watch)
#define STATUS_OK 0
#define STATUS_NOT_CONFIGURED 1
#define STATUS_NETWORK_ERROR 2
#define STATUS_SERVER_ERROR 3
#define STATUS_FETCHING 4

typedef struct {
  char author[AUTHOR_LEN];
  char text[TEXT_LEN];
  char time_ago[TIME_LEN];
  bool liked;
} Tweet;

static Tweet s_tweets[MAX_TWEETS];
static int s_tweet_count = 0;
static bool s_got_reply = false;
static int s_fetch_retries = 0;
static int s_feed = FEED_FOLLOWING;

#define PERSIST_FEED 1

static Window *s_timeline_window;
static MenuLayer *s_menu_layer;
static TextLayer *s_status_layer;
static char s_status_text[80];

static Window *s_detail_window;
static ScrollLayer *s_scroll_layer;
static TextLayer *s_detail_header_layer;
static TextLayer *s_detail_body_layer;
static TextLayer *s_detail_footer_layer;
static bool s_detail_open = false;
static int s_detail_index = -1;
static char s_detail_header[AUTHOR_LEN + TIME_LEN + 8];

static void prv_send_cmd(int cmd);

// Trim a truncated UTF-8 string so it doesn't end mid-sequence
static void prv_fix_utf8_tail(char *s) {
  size_t len = strlen(s);
  while (len > 0 && (s[len - 1] & 0xC0) == 0x80) {
    len--;
  }
  if (len > 0 && (s[len - 1] & 0xC0) == 0xC0) {
    len--;
  }
  s[len] = '\0';
}

static void prv_set_status(const char *text) {
  snprintf(s_status_text, sizeof(s_status_text), "%s", text);
  if (s_status_layer) {
    text_layer_set_text(s_status_layer, s_status_text);
    layer_set_hidden(text_layer_get_layer(s_status_layer), s_tweet_count > 0);
  }
}

// ---- Detail window ----

static void prv_update_detail_footer(void) {
  if (!s_detail_open || s_detail_index < 0) {
    return;
  }
  text_layer_set_text(s_detail_footer_layer,
                      s_tweets[s_detail_index].liked ? "<3 Liked" : "SELECT to like");
}

static void prv_detail_select_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_detail_index < 0 || s_tweets[s_detail_index].liked) {
    return;
  }
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) == APP_MSG_OK) {
    dict_write_int32(iter, MESSAGE_KEY_LIKE_INDEX, s_detail_index);
    app_message_outbox_send();
    text_layer_set_text(s_detail_footer_layer, "Liking...");
  }
}

static void prv_detail_click_config(void *context) {
  window_single_click_subscribe(BUTTON_ID_SELECT, prv_detail_select_handler);
}

static void prv_detail_window_load(Window *window) {
  Layer *window_layer = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(window_layer);
  const int margin = PBL_IF_ROUND_ELSE(16, 6);
  const int width = bounds.size.w - margin * 2;

  Tweet *t = &s_tweets[s_detail_index];
  snprintf(s_detail_header, sizeof(s_detail_header), "@%s · %s", t->author, t->time_ago);

  s_scroll_layer = scroll_layer_create(bounds);
  scroll_layer_set_shadow_hidden(s_scroll_layer, false);
  scroll_layer_set_callbacks(s_scroll_layer, (ScrollLayerCallbacks) {
    .click_config_provider = prv_detail_click_config,
  });
  scroll_layer_set_click_config_onto_window(s_scroll_layer, window);

  s_detail_header_layer = text_layer_create(GRect(margin, 2, width, 22));
  text_layer_set_font(s_detail_header_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
  text_layer_set_text(s_detail_header_layer, s_detail_header);
  scroll_layer_add_child(s_scroll_layer, text_layer_get_layer(s_detail_header_layer));

  GFont body_font = fonts_get_system_font(FONT_KEY_GOTHIC_18);
  GSize body_size = graphics_text_layout_get_content_size(
      t->text, body_font, GRect(0, 0, width, 2000), GTextOverflowModeWordWrap, GTextAlignmentLeft);
  s_detail_body_layer = text_layer_create(GRect(margin, 26, width, body_size.h + 6));
  text_layer_set_font(s_detail_body_layer, body_font);
  text_layer_set_text(s_detail_body_layer, t->text);
  scroll_layer_add_child(s_scroll_layer, text_layer_get_layer(s_detail_body_layer));

  const int footer_y = 26 + body_size.h + 10;
  s_detail_footer_layer = text_layer_create(GRect(margin, footer_y, width, 20));
  text_layer_set_font(s_detail_footer_layer, fonts_get_system_font(FONT_KEY_GOTHIC_14));
  text_layer_set_text_color(s_detail_footer_layer, PBL_IF_COLOR_ELSE(GColorDarkGray, GColorBlack));
  scroll_layer_add_child(s_scroll_layer, text_layer_get_layer(s_detail_footer_layer));

  scroll_layer_set_content_size(s_scroll_layer, GSize(bounds.size.w, footer_y + 28));
  layer_add_child(window_layer, scroll_layer_get_layer(s_scroll_layer));

  s_detail_open = true;
  prv_update_detail_footer();
}

static void prv_detail_window_unload(Window *window) {
  s_detail_open = false;
  text_layer_destroy(s_detail_header_layer);
  text_layer_destroy(s_detail_body_layer);
  text_layer_destroy(s_detail_footer_layer);
  scroll_layer_destroy(s_scroll_layer);
  window_destroy(window);
  s_detail_window = NULL;
}

static void prv_show_detail(int index) {
  s_detail_index = index;
  s_detail_window = window_create();
  window_set_window_handlers(s_detail_window, (WindowHandlers) {
    .load = prv_detail_window_load,
    .unload = prv_detail_window_unload,
  });
  window_stack_push(s_detail_window, true);
}

// ---- Timeline window ----
// Row 0 is a feed toggle; tweets occupy rows 1..count. A section header shows
// the current feed name.

static const char *prv_feed_name(int feed) {
  return feed == FEED_FORYOU ? "For You" : "Following";
}

static int prv_row_to_tweet(int row) {
  return row - 1;  // row 0 is the toggle
}

static uint16_t prv_get_num_rows(MenuLayer *menu_layer, uint16_t section_index, void *context) {
  return s_tweet_count + 1;
}

static int16_t prv_get_header_height(MenuLayer *menu_layer, uint16_t section_index, void *context) {
  return MENU_CELL_BASIC_HEADER_HEIGHT;
}

static void prv_draw_header(GContext *ctx, const Layer *cell_layer, uint16_t section_index,
                            void *context) {
  char title[24];
  snprintf(title, sizeof(title), "%s timeline", prv_feed_name(s_feed));
  menu_cell_basic_header_draw(ctx, cell_layer, title);
}

static int16_t prv_get_cell_height(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  return cell_index->row == 0 ? 34 : 56;
}

static void prv_draw_row(GContext *ctx, const Layer *cell_layer, MenuIndex *cell_index, void *context) {
  GRect bounds = layer_get_bounds(cell_layer);
  bool highlighted = menu_cell_layer_is_highlighted(cell_layer);
  graphics_context_set_text_color(ctx, highlighted ? GColorWhite : GColorBlack);

  if (cell_index->row == 0) {
    char toggle[28];
    snprintf(toggle, sizeof(toggle), "Switch to %s",
             prv_feed_name(s_feed == FEED_FORYOU ? FEED_FOLLOWING : FEED_FORYOU));
    graphics_draw_text(ctx, toggle, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                       GRect(6, 2, bounds.size.w - 12, 28), GTextOverflowModeTrailingEllipsis,
                       GTextAlignmentCenter, NULL);
    return;
  }

  Tweet *t = &s_tweets[prv_row_to_tweet(cell_index->row)];

  char author_line[AUTHOR_LEN + TIME_LEN + 10];
  snprintf(author_line, sizeof(author_line), "@%s · %s%s",
           t->author, t->time_ago, t->liked ? " <3" : "");

  char snippet[72];
  size_t j = 0;
  for (const char *p = t->text; *p && j < sizeof(snippet) - 1; p++) {
    snippet[j++] = (*p == '\n') ? ' ' : *p;
  }
  snippet[j] = '\0';
  prv_fix_utf8_tail(snippet);

  graphics_draw_text(ctx, author_line, fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD),
                     GRect(6, 0, bounds.size.w - 12, 16), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  graphics_draw_text(ctx, snippet, fonts_get_system_font(FONT_KEY_GOTHIC_14),
                     GRect(6, 16, bounds.size.w - 12, 36), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
}

static void prv_toggle_feed(void) {
  s_feed = (s_feed == FEED_FORYOU) ? FEED_FOLLOWING : FEED_FORYOU;
  persist_write_int(PERSIST_FEED, s_feed);
  s_tweet_count = 0;
  menu_layer_reload_data(s_menu_layer);
  menu_layer_set_selected_index(s_menu_layer, (MenuIndex) { .section = 0, .row = 0 },
                                MenuRowAlignTop, false);
  prv_set_status("Loading...");
  prv_send_cmd(CMD_FETCH);  // pkjs sends cached feed instantly if present
}

static void prv_select_click(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  if (cell_index->row == 0) {
    prv_toggle_feed();
  } else {
    prv_show_detail(prv_row_to_tweet(cell_index->row));
  }
}

static void prv_select_long_click(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  prv_set_status("Refreshing...");
  prv_send_cmd(CMD_REFRESH);
}

static void prv_timeline_window_load(Window *window) {
  Layer *window_layer = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(window_layer);

  s_menu_layer = menu_layer_create(bounds);
  menu_layer_set_callbacks(s_menu_layer, NULL, (MenuLayerCallbacks) {
    .get_num_rows = prv_get_num_rows,
    .get_header_height = prv_get_header_height,
    .draw_header = prv_draw_header,
    .get_cell_height = prv_get_cell_height,
    .draw_row = prv_draw_row,
    .select_click = prv_select_click,
    .select_long_click = prv_select_long_click,
  });
  menu_layer_set_click_config_onto_window(s_menu_layer, window);
  layer_add_child(window_layer, menu_layer_get_layer(s_menu_layer));

  const int margin = PBL_IF_ROUND_ELSE(16, 8);
  s_status_layer = text_layer_create(GRect(margin, bounds.size.h / 2 - 34,
                                           bounds.size.w - margin * 2, 68));
  text_layer_set_font(s_status_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18));
  text_layer_set_text_alignment(s_status_layer, GTextAlignmentCenter);
  layer_add_child(window_layer, text_layer_get_layer(s_status_layer));
  prv_set_status("Loading...");
}

static void prv_timeline_window_unload(Window *window) {
  text_layer_destroy(s_status_layer);
  s_status_layer = NULL;
  menu_layer_destroy(s_menu_layer);
}

// ---- AppMessage ----

static void prv_send_cmd(int cmd) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) == APP_MSG_OK) {
    dict_write_int32(iter, MESSAGE_KEY_CMD, cmd);
    dict_write_int32(iter, MESSAGE_KEY_FEED, s_feed);
    app_message_outbox_send();
  }
}

static void prv_retry_fetch(void *context) {
  if (!s_got_reply && s_fetch_retries++ < 5) {
    prv_send_cmd(CMD_FETCH);
    app_timer_register(1000, prv_retry_fetch, NULL);
  }
}

static void prv_inbox_received(DictionaryIterator *iter, void *context) {
  s_got_reply = true;
  Tuple *t;

  if ((t = dict_find(iter, MESSAGE_KEY_STATUS))) {
    switch (t->value->int32) {
      case STATUS_NOT_CONFIGURED:
        prv_set_status("Not set up.\nOpen the app settings on your phone.");
        break;
      case STATUS_NETWORK_ERROR:
        prv_set_status("Network error.\nHold SELECT to retry.");
        break;
      case STATUS_SERVER_ERROR:
        prv_set_status("Server error.\nHold SELECT to retry.");
        break;
      case STATUS_FETCHING:
        prv_set_status("Refreshing...");
        break;
      default:
        break;
    }
  }

  if ((t = dict_find(iter, MESSAGE_KEY_TWEET_COUNT))) {
    int count = t->value->int32;
    s_tweet_count = 0;
    if (count == 0) {
      prv_set_status("Timeline is empty.");
    }
    menu_layer_reload_data(s_menu_layer);
  }

  if ((t = dict_find(iter, MESSAGE_KEY_TWEET_INDEX))) {
    int index = t->value->int32;
    if (index >= 0 && index < MAX_TWEETS) {
      Tweet *tweet = &s_tweets[index];
      Tuple *field;
      if ((field = dict_find(iter, MESSAGE_KEY_AUTHOR))) {
        snprintf(tweet->author, sizeof(tweet->author), "%s", field->value->cstring);
        prv_fix_utf8_tail(tweet->author);
      }
      if ((field = dict_find(iter, MESSAGE_KEY_TEXT))) {
        snprintf(tweet->text, sizeof(tweet->text), "%s", field->value->cstring);
        prv_fix_utf8_tail(tweet->text);
      }
      if ((field = dict_find(iter, MESSAGE_KEY_TIME_AGO))) {
        snprintf(tweet->time_ago, sizeof(tweet->time_ago), "%s", field->value->cstring);
      }
      if ((field = dict_find(iter, MESSAGE_KEY_LIKED))) {
        tweet->liked = field->value->int32 != 0;
      }
      if (index + 1 > s_tweet_count) {
        s_tweet_count = index + 1;
      }
      menu_layer_reload_data(s_menu_layer);
      layer_set_hidden(text_layer_get_layer(s_status_layer), true);
    }
  }

  if ((t = dict_find(iter, MESSAGE_KEY_LIKE_RESULT))) {
    int index = t->value->int32;
    if (index >= 0 && index < s_tweet_count) {
      s_tweets[index].liked = true;
      vibes_short_pulse();
      menu_layer_reload_data(s_menu_layer);
      prv_update_detail_footer();
    } else if (s_detail_open) {
      text_layer_set_text(s_detail_footer_layer, "Like failed");
    }
  }
}

// ---- App lifecycle ----

static void prv_init(void) {
  s_feed = persist_exists(PERSIST_FEED) ? persist_read_int(PERSIST_FEED) : FEED_FOLLOWING;

  s_timeline_window = window_create();
  window_set_window_handlers(s_timeline_window, (WindowHandlers) {
    .load = prv_timeline_window_load,
    .unload = prv_timeline_window_unload,
  });
  window_stack_push(s_timeline_window, true);

  app_message_register_inbox_received(prv_inbox_received);
  app_message_open(1024, 64);

  prv_send_cmd(CMD_FETCH);
  app_timer_register(1000, prv_retry_fetch, NULL);
}

static void prv_deinit(void) {
  window_destroy(s_timeline_window);
}

int main(void) {
  prv_init();
  app_event_loop();
  prv_deinit();
}
