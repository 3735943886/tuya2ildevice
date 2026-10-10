#ifndef TUYA_RULE_ENGINE_H
#define TUYA_RULE_ENGINE_H
#ifdef __cplusplus
extern "C" {
#endif
/* Borrowed UTF-8 NUL-terminated request, owned UTF-8 JSON response.
   Release every response exactly once with tuya_engine_free from this library.
   Null input returns an error; free(NULL) is allowed. Invalid pointers are UB. */
char *tuya_engine_eval(const char *request_json);
void tuya_engine_free(char *response_json);
#ifdef __cplusplus
}
#endif
#endif
