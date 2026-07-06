#include <pebble.h>
#include <stdlib.h>
#include <string.h>

#define MAX_TWEETS 15
#define AUTHOR_LEN 24
#define TEXT_LEN 441   // UTF-8 bytes; pkjs truncates to fit
#define TIME_LEN 8

// Commands (watch -> phone)
#define CMD_FETCH 0
#define CMD_REFRESH 1
#define CMD_IMAGE 2

// Feeds
#define FEED_FOLLOWING 0
#define FEED_FORYOU 1

// Status codes (phone -> watch)
#define STATUS_OK 0
#define STATUS_NOT_CONFIGURED 1
#define STATUS_NETWORK_ERROR 2
#define STATUS_SERVER_ERROR 3
#define STATUS_FETCHING 4

// Image transfer status codes (phone -> watch)
#define IMAGE_ERROR_MISSING 1
#define IMAGE_ERROR_NETWORK 2
#define IMAGE_ERROR_SERVER 3
#define IMAGE_ERROR_DECODE 4

#define IMAGE_MAX_BYTES (32 * 1024)

#define ACTION_ICON_SIZE 25
#define ACTION_ICON_MARGIN 8
#define ACTION_OVERLAY_WIDTH (ACTION_ICON_SIZE + ACTION_ICON_MARGIN * 2)

// Closest Pebble 64-color match to X/Twitter blue #1DA1F2; black on B&W.
#define ACCENT_COLOR PBL_IF_COLOR_ELSE(GColorVividCerulean, GColorBlack)

typedef struct {
  char author[AUTHOR_LEN];
  char text[TEXT_LEN];
  char time_ago[TIME_LEN];
  bool liked;
  bool has_media;
  int media_count;
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

// Timeline action overlay (opened from the feed status row): switch feed / refresh.
static Layer *s_feed_overlay_layer;
static GBitmap *s_swap_bitmap;
static GBitmap *s_refresh_bitmap;

// Refresh-in-progress indicator on the feed status row (animated dots).
static bool s_refreshing = false;
static int s_refresh_dots = 1;
static AppTimer *s_refresh_timer = NULL;

static Window *s_detail_window;
static ScrollLayer *s_scroll_layer;
static TextLayer *s_detail_header_layer;
static TextLayer *s_detail_body_layer;
static int s_detail_index = -1;
static char s_detail_header[AUTHOR_LEN + TIME_LEN + 8];

static Layer *s_action_overlay_layer;
static bool s_action_open = false;
static int s_action_index = -1;
static bool s_action_show_image = false;
static GBitmap *s_action_retweet_bitmap;
static GBitmap *s_action_like_bitmap;
static GBitmap *s_action_image_bitmap;

static Window *s_image_window;
static BitmapLayer *s_image_bitmap_layer;
static TextLayer *s_image_status_layer;
static char s_image_status_text[64];
static GBitmap *s_image_bitmap;
static uint8_t *s_image_png_data;
static int s_image_expected_bytes = 0;
static int s_image_received_bytes = 0;
static bool s_image_open = false;
static int s_image_tweet_index = -1;
static int s_image_media_index = 0;
static int s_image_media_count = 0;
static int s_image_request_id = 0;

static void prv_send_cmd(int cmd);
static void prv_show_action(int index);
static void prv_hide_action(void);
static void prv_action_retweet(void);
static void prv_action_like(void);
static void prv_action_images(void);
static void prv_action_set_labels(void);
static void prv_action_overlay_update_proc(Layer *layer, GContext *ctx);
static void prv_show_image(int index);

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

static void prv_detail_up_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_action_open) {
    if (!click_recognizer_is_repeating(recognizer)) {
      prv_action_retweet();
      prv_hide_action();
    }
    return;
  }
  scroll_layer_scroll_up_click_handler(recognizer, s_scroll_layer);
}

static void prv_detail_down_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_action_open) {
    if (!click_recognizer_is_repeating(recognizer)) {
      prv_action_images();
      prv_hide_action();
    }
    return;
  }
  scroll_layer_scroll_down_click_handler(recognizer, s_scroll_layer);
}

static void prv_detail_select_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_action_open) {
    prv_action_like();
    prv_hide_action();
  } else if (s_detail_index >= 0) {
    prv_show_action(s_detail_index);
  }
}

static void prv_detail_back_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_action_open) {
    prv_hide_action();
  } else {
    window_stack_pop(true);
  }
}

static void prv_detail_click_config(void *context) {
  window_single_repeating_click_subscribe(BUTTON_ID_UP, 100, prv_detail_up_handler);
  window_single_repeating_click_subscribe(BUTTON_ID_DOWN, 100, prv_detail_down_handler);
  window_single_click_subscribe(BUTTON_ID_SELECT, prv_detail_select_handler);
  window_single_click_subscribe(BUTTON_ID_BACK, prv_detail_back_handler);
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

  s_detail_header_layer = text_layer_create(GRect(margin, 2, width, 24));
  text_layer_set_font(s_detail_header_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
  text_layer_set_text_color(s_detail_header_layer, ACCENT_COLOR);
  text_layer_set_text(s_detail_header_layer, s_detail_header);
  scroll_layer_add_child(s_scroll_layer, text_layer_get_layer(s_detail_header_layer));

  GFont body_font = fonts_get_system_font(FONT_KEY_GOTHIC_24);
  GSize body_size = graphics_text_layout_get_content_size(
      t->text, body_font, GRect(0, 0, width, 2000), GTextOverflowModeWordWrap, GTextAlignmentLeft);
  s_detail_body_layer = text_layer_create(GRect(margin, 28, width, body_size.h + 8));
  text_layer_set_font(s_detail_body_layer, body_font);
  text_layer_set_text(s_detail_body_layer, t->text);
  scroll_layer_add_child(s_scroll_layer, text_layer_get_layer(s_detail_body_layer));

  scroll_layer_set_content_size(s_scroll_layer, GSize(bounds.size.w, 28 + body_size.h + 16));
  layer_add_child(window_layer, scroll_layer_get_layer(s_scroll_layer));

  s_action_overlay_layer = layer_create(GRect(bounds.size.w - ACTION_OVERLAY_WIDTH, 0,
                                              ACTION_OVERLAY_WIDTH, bounds.size.h));
  layer_set_update_proc(s_action_overlay_layer, prv_action_overlay_update_proc);
  layer_set_hidden(s_action_overlay_layer, true);
  layer_add_child(window_layer, s_action_overlay_layer);

  s_action_retweet_bitmap = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_RETWEET_ICON);
  s_action_like_bitmap = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_HEART_ICON);
  s_action_image_bitmap = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_IMG_ICON);
}

static void prv_detail_window_unload(Window *window) {
  prv_hide_action();
  gbitmap_destroy(s_action_retweet_bitmap);
  gbitmap_destroy(s_action_like_bitmap);
  gbitmap_destroy(s_action_image_bitmap);
  s_action_retweet_bitmap = NULL;
  s_action_like_bitmap = NULL;
  s_action_image_bitmap = NULL;
  layer_destroy(s_action_overlay_layer);
  s_action_overlay_layer = NULL;
  text_layer_destroy(s_detail_header_layer);
  text_layer_destroy(s_detail_body_layer);
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

// ---- Action overlay ----

static bool prv_action_has_images(void) {
  return PBL_IF_COLOR_ELSE(
      s_action_index >= 0 && s_action_index < s_tweet_count &&
      s_tweets[s_action_index].media_count > 0,
      false);
}

static void prv_action_mark_dirty(void) {
  if (s_action_overlay_layer) {
    layer_mark_dirty(s_action_overlay_layer);
  }
}

static void prv_action_set_retweet_text(const char *text) {
  (void) text;
  prv_action_mark_dirty();
}

static void prv_action_set_like_text(const char *text) {
  (void) text;
  prv_action_mark_dirty();
}

static void prv_action_set_labels(void) {
  if (s_action_index < 0 || s_action_index >= s_tweet_count) {
    return;
  }
  s_action_show_image = prv_action_has_images();
  prv_action_mark_dirty();
}

static void prv_action_draw_icon(GContext *ctx, GBitmap *bitmap, int center_y, int width) {
  if (!bitmap) {
    return;
  }
  GSize size = gbitmap_get_bounds(bitmap).size;
  int x = (width - size.w) / 2;
  int y = center_y - size.h / 2;
  graphics_draw_bitmap_in_rect(ctx, bitmap, GRect(x, y, size.w, size.h));
}

static void prv_action_overlay_update_proc(Layer *layer, GContext *ctx) {
  GRect bounds = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, bounds, 0, GCornerNone);

  int top_center = bounds.size.h / 5;
  int middle_center = bounds.size.h / 2;
  int bottom_center = bounds.size.h - top_center;
  prv_action_draw_icon(ctx, s_action_retweet_bitmap, top_center, bounds.size.w);
  prv_action_draw_icon(ctx, s_action_like_bitmap, middle_center, bounds.size.w);
  if (s_action_show_image) {
    prv_action_draw_icon(ctx, s_action_image_bitmap, bottom_center, bounds.size.w);
  }
}

static void prv_hide_action(void) {
  s_action_open = false;
  s_action_index = -1;
  s_action_show_image = false;
  if (s_action_overlay_layer) {
    layer_set_hidden(s_action_overlay_layer, true);
  }
}

static void prv_show_action(int index) {
  if (!s_action_overlay_layer || index < 0 || index >= s_tweet_count) {
    return;
  }
  s_action_index = index;
  s_action_open = true;
  prv_action_set_labels();
  layer_set_hidden(s_action_overlay_layer, false);
}

static bool prv_send_index_message(uint32_t key, int index) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, key, index);
  app_message_outbox_send();
  return true;
}

static void prv_action_retweet(void) {
  if (s_action_index < 0 || s_action_index >= s_tweet_count) {
    return;
  }
  if (prv_send_index_message(MESSAGE_KEY_RETWEET_INDEX, s_action_index)) {
    prv_action_set_retweet_text("retweeting...");
  } else {
    prv_action_set_retweet_text("phone busy");
  }
}

static void prv_action_like(void) {
  if (s_action_index < 0 || s_action_index >= s_tweet_count) {
    return;
  }
  if (s_tweets[s_action_index].liked) {
    prv_action_set_like_text("liked");
    return;
  }
  if (prv_send_index_message(MESSAGE_KEY_LIKE_INDEX, s_action_index)) {
    prv_action_set_like_text("liking...");
  } else {
    prv_action_set_like_text("phone busy");
  }
}

static void prv_action_images(void) {
  if (prv_action_has_images()) {
    prv_show_image(s_action_index);
  }
}

// ---- Image window ----

static void prv_clear_image_transfer(void) {
  if (s_image_png_data) {
    free(s_image_png_data);
    s_image_png_data = NULL;
  }
  s_image_expected_bytes = 0;
  s_image_received_bytes = 0;
}

static void prv_clear_image_bitmap(void) {
  if (s_image_bitmap_layer) {
    bitmap_layer_set_bitmap(s_image_bitmap_layer, NULL);
  }
  if (s_image_bitmap) {
    gbitmap_destroy(s_image_bitmap);
    s_image_bitmap = NULL;
  }
}

static void prv_set_image_status(const char *text) {
  snprintf(s_image_status_text, sizeof(s_image_status_text), "%s", text);
  if (s_image_status_layer) {
    text_layer_set_text(s_image_status_layer, s_image_status_text);
    layer_set_hidden(text_layer_get_layer(s_image_status_layer), false);
  }
  if (s_image_bitmap_layer) {
    layer_set_hidden(bitmap_layer_get_layer(s_image_bitmap_layer), true);
  }
}

static void prv_set_image_progress_status(const char *line) {
  char text[64];
  if (s_image_media_count > 1) {
    snprintf(text, sizeof(text), "Photo %d/%d\n%s", s_image_media_index + 1,
             s_image_media_count, line);
  } else {
    snprintf(text, sizeof(text), "%s", line);
  }
  prv_set_image_status(text);
}

static bool prv_send_image_cmd(GSize size) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, CMD_IMAGE);
  dict_write_int32(iter, MESSAGE_KEY_FEED, s_feed);
  dict_write_int32(iter, MESSAGE_KEY_TWEET_INDEX, s_image_tweet_index);
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_INDEX, s_image_media_index);
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_W, size.w);
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_H, size.h);
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_COLOR, PBL_IF_COLOR_ELSE(1, 0));
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_ID, s_image_request_id);
  dict_write_int32(iter, MESSAGE_KEY_IMAGE_HEAP, (int)heap_bytes_free());
  app_message_outbox_send();
  return true;
}

static void prv_request_current_image(void) {
  if (!s_image_window) {
    return;
  }
  GRect bounds = layer_get_bounds(window_get_root_layer(s_image_window));
  s_image_request_id++;
  prv_clear_image_transfer();
  prv_clear_image_bitmap();
  prv_set_image_progress_status("Loading...");
  if (!prv_send_image_cmd(bounds.size)) {
    prv_set_image_status("Phone busy.\nTry again.");
  }
}

static void prv_image_up_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_image_media_index > 0) {
    s_image_media_index--;
    prv_request_current_image();
  }
}

static void prv_image_down_handler(ClickRecognizerRef recognizer, void *context) {
  if (s_image_media_index + 1 < s_image_media_count) {
    s_image_media_index++;
    prv_request_current_image();
  }
}

static void prv_image_click_config(void *context) {
  if (s_image_media_count > 1) {
    window_single_click_subscribe(BUTTON_ID_UP, prv_image_up_handler);
    window_single_click_subscribe(BUTTON_ID_DOWN, prv_image_down_handler);
  }
}

static void prv_image_window_load(Window *window) {
  Layer *window_layer = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(window_layer);
  const int margin = PBL_IF_ROUND_ELSE(18, 8);

  s_image_open = true;
  s_image_bitmap_layer = bitmap_layer_create(bounds);
  bitmap_layer_set_alignment(s_image_bitmap_layer, GAlignCenter);
  bitmap_layer_set_background_color(s_image_bitmap_layer, GColorBlack);
  bitmap_layer_set_compositing_mode(s_image_bitmap_layer, GCompOpSet);
  layer_set_hidden(bitmap_layer_get_layer(s_image_bitmap_layer), true);
  layer_add_child(window_layer, bitmap_layer_get_layer(s_image_bitmap_layer));

  s_image_status_layer = text_layer_create(GRect(margin, bounds.size.h / 2 - 34,
                                                 bounds.size.w - margin * 2, 68));
  text_layer_set_background_color(s_image_status_layer, GColorBlack);
  text_layer_set_text_color(s_image_status_layer, GColorWhite);
  text_layer_set_font(s_image_status_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18));
  text_layer_set_text_alignment(s_image_status_layer, GTextAlignmentCenter);
  layer_add_child(window_layer, text_layer_get_layer(s_image_status_layer));

  prv_clear_image_transfer();
  prv_clear_image_bitmap();
  prv_request_current_image();
}

static void prv_image_window_unload(Window *window) {
  s_image_open = false;
  s_image_request_id++;
  prv_clear_image_transfer();
  prv_clear_image_bitmap();
  bitmap_layer_destroy(s_image_bitmap_layer);
  s_image_bitmap_layer = NULL;
  text_layer_destroy(s_image_status_layer);
  s_image_status_layer = NULL;
  s_image_tweet_index = -1;
  s_image_media_index = 0;
  s_image_media_count = 0;
  window_destroy(window);
  s_image_window = NULL;
}

static void prv_show_image(int index) {
  if (!PBL_IF_COLOR_ELSE(true, false) || s_image_window || index < 0 ||
      index >= s_tweet_count || s_tweets[index].media_count <= 0) {
    return;
  }
  s_image_tweet_index = index;
  s_image_media_index = 0;
  s_image_media_count = s_tweets[index].media_count;
  s_image_window = window_create();
  window_set_background_color(s_image_window, GColorBlack);
  window_set_window_handlers(s_image_window, (WindowHandlers) {
    .load = prv_image_window_load,
    .unload = prv_image_window_unload,
  });
  window_set_click_config_provider(s_image_window, prv_image_click_config);
  window_stack_push(s_image_window, true);
}

// ---- Timeline window ----
// Row 0 is a status row showing the current feed; SELECT on it opens the feed
// action overlay (switch feed / refresh). Tweets occupy rows 1..count.

static const char *prv_feed_name(int feed) {
  return feed == FEED_FORYOU ? "For You" : "Following";
}

static int prv_row_to_tweet(int row) {
  return row - 1;  // row 0 is the feed status row
}

static uint16_t prv_get_num_rows(MenuLayer *menu_layer, uint16_t section_index, void *context) {
  return s_tweet_count + 1;
}

static int16_t prv_get_cell_height(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  return cell_index->row == 0 ? 34 : 68;
}

static void prv_refresh_tick(void *context) {
  s_refresh_dots = (s_refresh_dots % 3) + 1;
  s_refresh_timer = app_timer_register(400, prv_refresh_tick, NULL);
  if (s_menu_layer) {
    layer_mark_dirty(menu_layer_get_layer(s_menu_layer));
  }
}

static void prv_set_refreshing(bool refreshing) {
  if (refreshing == s_refreshing) {
    return;
  }
  s_refreshing = refreshing;
  s_refresh_dots = 1;
  if (refreshing) {
    s_refresh_timer = app_timer_register(400, prv_refresh_tick, NULL);
  } else if (s_refresh_timer) {
    app_timer_cancel(s_refresh_timer);
    s_refresh_timer = NULL;
  }
  if (s_menu_layer) {
    layer_mark_dirty(menu_layer_get_layer(s_menu_layer));
  }
}

static void prv_draw_row(GContext *ctx, const Layer *cell_layer, MenuIndex *cell_index, void *context) {
  GRect bounds = layer_get_bounds(cell_layer);
  bool highlighted = menu_cell_layer_is_highlighted(cell_layer);

  if (cell_index->row == 0) {
    // Status row: shows the CURRENT feed; SELECT opens the action overlay.
    // While a refresh is in flight it shows animated "Refreshing..." instead.
    GColor fg = highlighted ? GColorWhite : ACCENT_COLOR;
    graphics_context_set_fill_color(ctx, fg);
    graphics_fill_circle(ctx, GPoint(13, bounds.size.h / 2 - 1), 3);
    graphics_context_set_text_color(ctx, fg);
    char label[16];
    snprintf(label, sizeof(label), "%s%.*s",
             s_refreshing ? "Refreshing" : prv_feed_name(s_feed),
             s_refreshing ? s_refresh_dots : 0, "...");
    graphics_draw_text(ctx, label, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                       GRect(22, 2, bounds.size.w - 44, 24), GTextOverflowModeTrailingEllipsis,
                       GTextAlignmentLeft, NULL);
    if (!s_refreshing) {
      graphics_draw_text(ctx, ">", fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                         GRect(bounds.size.w - 20, 2, 14, 24), GTextOverflowModeTrailingEllipsis,
                         GTextAlignmentRight, NULL);
    }
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

  graphics_context_set_text_color(ctx, highlighted ? GColorWhite : ACCENT_COLOR);
  graphics_draw_text(ctx, author_line, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD),
                     GRect(6, -2, bounds.size.w - 12, 20), GTextOverflowModeTrailingEllipsis,
                     GTextAlignmentLeft, NULL);
  graphics_context_set_text_color(ctx, highlighted ? GColorWhite : GColorBlack);
  graphics_draw_text(ctx, snippet, fonts_get_system_font(FONT_KEY_GOTHIC_18),
                     GRect(6, 18, bounds.size.w - 12, 46), GTextOverflowModeTrailingEllipsis,
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

// ---- Feed action overlay ----
// Same right-edge strip as the detail window: UP = switch feed, SELECT = refresh.

// The timeline's normal click config wraps the menu layer's provider so BACK is
// always explicitly bound to "exit app". Relying on the implicit default doesn't
// work here: once the overlay subscribes BACK, restoring the menu provider leaves
// that stale subscription in place (the menu provider never touches BACK).
static ClickConfigProvider s_menu_click_config;
static void *s_menu_click_context;

static void prv_timeline_back_handler(ClickRecognizerRef recognizer, void *context) {
  window_stack_pop(true);  // timeline is the root window, so this exits the app
}

static void prv_timeline_click_config(void *context) {
  s_menu_click_config(context);
  window_single_click_subscribe(BUTTON_ID_BACK, prv_timeline_back_handler);
}

static void prv_feed_overlay_update_proc(Layer *layer, GContext *ctx) {
  GRect bounds = layer_get_bounds(layer);
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, bounds, 0, GCornerNone);
  prv_action_draw_icon(ctx, s_swap_bitmap, bounds.size.h / 5, bounds.size.w);
  prv_action_draw_icon(ctx, s_refresh_bitmap, bounds.size.h / 2, bounds.size.w);
}

static void prv_hide_feed_action(void) {
  if (s_feed_overlay_layer) {
    layer_set_hidden(s_feed_overlay_layer, true);
  }
  window_set_click_config_provider_with_context(s_timeline_window, prv_timeline_click_config,
                                                s_menu_click_context);
}

static void prv_feed_action_up_handler(ClickRecognizerRef recognizer, void *context) {
  prv_hide_feed_action();
  prv_toggle_feed();
}

static void prv_feed_action_select_handler(ClickRecognizerRef recognizer, void *context) {
  prv_hide_feed_action();
  prv_set_status("Refreshing...");
  prv_set_refreshing(true);
  prv_send_cmd(CMD_REFRESH);
}

static void prv_feed_action_back_handler(ClickRecognizerRef recognizer, void *context) {
  prv_hide_feed_action();
}

static void prv_feed_action_click_config(void *context) {
  window_single_click_subscribe(BUTTON_ID_UP, prv_feed_action_up_handler);
  window_single_click_subscribe(BUTTON_ID_SELECT, prv_feed_action_select_handler);
  window_single_click_subscribe(BUTTON_ID_BACK, prv_feed_action_back_handler);
}

static void prv_show_feed_action(void) {
  if (!s_feed_overlay_layer) {
    return;
  }
  layer_set_hidden(s_feed_overlay_layer, false);
  window_set_click_config_provider(s_timeline_window, prv_feed_action_click_config);
}

static void prv_select_click(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  if (cell_index->row == 0) {
    prv_show_feed_action();
  } else {
    prv_show_detail(prv_row_to_tweet(cell_index->row));
  }
}

static void prv_select_long_click(MenuLayer *menu_layer, MenuIndex *cell_index, void *context) {
  prv_set_status("Refreshing...");
  prv_set_refreshing(true);
  prv_send_cmd(CMD_REFRESH);
}

static void prv_timeline_window_load(Window *window) {
  Layer *window_layer = window_get_root_layer(window);
  GRect bounds = layer_get_bounds(window_layer);

  s_menu_layer = menu_layer_create(bounds);
  menu_layer_set_callbacks(s_menu_layer, NULL, (MenuLayerCallbacks) {
    .get_num_rows = prv_get_num_rows,
    .get_cell_height = prv_get_cell_height,
    .draw_row = prv_draw_row,
    .select_click = prv_select_click,
    .select_long_click = prv_select_long_click,
  });
  menu_layer_set_highlight_colors(s_menu_layer, GColorBlack, GColorWhite);
  menu_layer_set_click_config_onto_window(s_menu_layer, window);
  // Wrap the menu's provider so BACK always exits (see prv_timeline_click_config).
  s_menu_click_config = window_get_click_config_provider(window);
  s_menu_click_context = window_get_click_config_context(window);
  window_set_click_config_provider_with_context(window, prv_timeline_click_config,
                                                s_menu_click_context);
  layer_add_child(window_layer, menu_layer_get_layer(s_menu_layer));

  const int margin = PBL_IF_ROUND_ELSE(16, 8);
  s_status_layer = text_layer_create(GRect(margin, bounds.size.h / 2 - 34,
                                           bounds.size.w - margin * 2, 68));
  text_layer_set_font(s_status_layer, fonts_get_system_font(FONT_KEY_GOTHIC_18));
  text_layer_set_text_alignment(s_status_layer, GTextAlignmentCenter);
  layer_add_child(window_layer, text_layer_get_layer(s_status_layer));
  prv_set_status("Loading...");

  s_feed_overlay_layer = layer_create(GRect(bounds.size.w - ACTION_OVERLAY_WIDTH, 0,
                                            ACTION_OVERLAY_WIDTH, bounds.size.h));
  layer_set_update_proc(s_feed_overlay_layer, prv_feed_overlay_update_proc);
  layer_set_hidden(s_feed_overlay_layer, true);
  layer_add_child(window_layer, s_feed_overlay_layer);

  s_swap_bitmap = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_SWAP_ICON);
  s_refresh_bitmap = gbitmap_create_with_resource(RESOURCE_ID_IMAGE_REFRESH_ICON);
}

static void prv_timeline_window_unload(Window *window) {
  prv_set_refreshing(false);
  gbitmap_destroy(s_swap_bitmap);
  gbitmap_destroy(s_refresh_bitmap);
  s_swap_bitmap = NULL;
  s_refresh_bitmap = NULL;
  layer_destroy(s_feed_overlay_layer);
  s_feed_overlay_layer = NULL;
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

static void prv_image_error(int code) {
  prv_clear_image_transfer();
  prv_clear_image_bitmap();
  switch (code) {
    case IMAGE_ERROR_MISSING:
      prv_set_image_status("No photo found.");
      break;
    case IMAGE_ERROR_NETWORK:
      prv_set_image_status("Network error.\nTry again.");
      break;
    case IMAGE_ERROR_DECODE:
      prv_set_image_status("Photo decode failed.");
      break;
    case IMAGE_ERROR_SERVER:
    default:
      prv_set_image_status("Photo failed.\nTry again.");
      break;
  }
}

static void prv_start_image_transfer(int total_bytes) {
  prv_clear_image_transfer();
  prv_clear_image_bitmap();
  if (total_bytes <= 0 || total_bytes > IMAGE_MAX_BYTES) {
    prv_set_image_status("Photo too large.");
    return;
  }
  s_image_png_data = malloc(total_bytes);
  if (!s_image_png_data) {
    prv_set_image_status("Not enough memory.");
    return;
  }
  s_image_expected_bytes = total_bytes;
  s_image_received_bytes = 0;
  prv_set_image_progress_status("0%");
}

static void prv_finish_image_transfer(void) {
  if (!s_image_png_data || s_image_expected_bytes <= 0) {
    return;
  }
  GBitmap *bitmap = gbitmap_create_from_png_data(s_image_png_data, s_image_expected_bytes);
  prv_clear_image_transfer();
  if (!bitmap) {
    prv_image_error(IMAGE_ERROR_DECODE);
    return;
  }
  prv_clear_image_bitmap();
  s_image_bitmap = bitmap;
  bitmap_layer_set_bitmap(s_image_bitmap_layer, s_image_bitmap);
  layer_set_hidden(text_layer_get_layer(s_image_status_layer), true);
  layer_set_hidden(bitmap_layer_get_layer(s_image_bitmap_layer), false);
}

static void prv_receive_image_chunk(DictionaryIterator *iter) {
  if (!s_image_png_data || s_image_expected_bytes <= 0) {
    return;
  }
  Tuple *offset_tuple = dict_find(iter, MESSAGE_KEY_IMAGE_OFFSET);
  Tuple *data_tuple = dict_find(iter, MESSAGE_KEY_IMAGE_DATA);
  if (!offset_tuple || !data_tuple || data_tuple->length == 0) {
    return;
  }

  int offset = offset_tuple->value->int32;
  int length = data_tuple->length;
  if (offset < 0 || offset + length > s_image_expected_bytes) {
    prv_image_error(IMAGE_ERROR_DECODE);
    return;
  }
  memcpy(s_image_png_data + offset, data_tuple->value->data, length);

  int end = offset + length;
  if (end > s_image_received_bytes) {
    s_image_received_bytes = end;
  }

  if (s_image_received_bytes >= s_image_expected_bytes) {
    prv_finish_image_transfer();
  } else if (s_image_status_layer) {
    int pct = (s_image_received_bytes * 100) / s_image_expected_bytes;
    char progress[12];
    snprintf(progress, sizeof(progress), "%d%%", pct);
    prv_set_image_progress_status(progress);
  }
}

static void prv_inbox_image_received(DictionaryIterator *iter, int request_id) {
  if (!s_image_open || request_id != s_image_request_id) {
    return;
  }

  Tuple *error_tuple = dict_find(iter, MESSAGE_KEY_IMAGE_ERROR);
  if (error_tuple) {
    prv_image_error(error_tuple->value->int32);
    return;
  }

  Tuple *total_tuple = dict_find(iter, MESSAGE_KEY_IMAGE_TOTAL);
  if (total_tuple) {
    prv_start_image_transfer(total_tuple->value->int32);
  }

  if (dict_find(iter, MESSAGE_KEY_IMAGE_DATA)) {
    prv_receive_image_chunk(iter);
  }
}

static void prv_inbox_received(DictionaryIterator *iter, void *context) {
  s_got_reply = true;
  Tuple *t;

  if ((t = dict_find(iter, MESSAGE_KEY_IMAGE_ID))) {
    prv_inbox_image_received(iter, t->value->int32);
  }

  if ((t = dict_find(iter, MESSAGE_KEY_STATUS))) {
    switch (t->value->int32) {
      case STATUS_NOT_CONFIGURED:
        prv_set_status("Not set up.\nOpen the app settings on your phone.");
        prv_set_refreshing(false);
        break;
      case STATUS_NETWORK_ERROR:
        prv_set_status("Network error.\nHold SELECT to retry.");
        prv_set_refreshing(false);
        break;
      case STATUS_SERVER_ERROR:
        prv_set_status("Server error.\nHold SELECT to retry.");
        prv_set_refreshing(false);
        break;
      case STATUS_FETCHING:
        prv_set_status("Refreshing...");
        prv_set_refreshing(true);
        break;
      default:
        prv_set_refreshing(false);
        break;
    }
  }

  if ((t = dict_find(iter, MESSAGE_KEY_TWEET_COUNT))) {
    int count = t->value->int32;
    s_tweet_count = 0;
    if (count == 0) {
      prv_set_status("Timeline is empty.");
    }
    prv_set_refreshing(false);
    menu_layer_reload_data(s_menu_layer);
  }

  if ((t = dict_find(iter, MESSAGE_KEY_TWEET_INDEX))) {
    int index = t->value->int32;
    if (index >= 0 && index < MAX_TWEETS) {
      Tweet *tweet = &s_tweets[index];
      tweet->has_media = false;
      tweet->media_count = 0;
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
      if ((field = dict_find(iter, MESSAGE_KEY_HAS_MEDIA))) {
        tweet->has_media = PBL_IF_COLOR_ELSE(field->value->int32 != 0, false);
        tweet->media_count = tweet->has_media ? 1 : 0;
      }
      if ((field = dict_find(iter, MESSAGE_KEY_MEDIA_COUNT))) {
        int count = field->value->int32;
        if (count < 0) {
          count = 0;
        }
        tweet->media_count = PBL_IF_COLOR_ELSE(count, 0);
        tweet->has_media = tweet->media_count > 0;
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
      if (s_action_open && s_action_index == index) {
        prv_action_set_like_text("liked");
      }
    } else {
      int failed_index = -index - 1;
      if (s_action_open && failed_index == s_action_index) {
        prv_action_set_like_text("like failed");
      }
    }
  }

  if ((t = dict_find(iter, MESSAGE_KEY_RETWEET_RESULT))) {
    int index = t->value->int32;
    if (index >= 0 && index < s_tweet_count) {
      vibes_short_pulse();
      if (s_action_open && s_action_index == index) {
        prv_action_set_retweet_text("retweeted");
      }
    } else {
      int failed_index = -index - 1;
      if (s_action_open && failed_index == s_action_index) {
        prv_action_set_retweet_text("retweet failed");
      }
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
  app_message_open(1024, 256);

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
