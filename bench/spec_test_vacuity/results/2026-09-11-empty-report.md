# spec_test_vacuity / probe_empty — 8 tasks, US$ 0.0806

- `shipped` (one call, no budget): module on **6 / 8**
- `retry` (explicit budget, one retry): module on **5 / 8**

| task | reqs | shipped | shipped finish / tokens | retry | attempts | retry finishes / tokens | s |
|---|---:|---|---|---|---:|---|---:|
| `fix_merge_settings` | 6 | module | stop/2064 | module | 1 | stop/3275 | 87.7 |
| `fix_count_words` | 7 | module | stop/2813 | module | 1 | stop/3141 | 170.0 |
| `fix_rotate_list` | 8 | **nothing** | length/131072 | module | 1 | stop/2464 | 436.4 |
| `fix_collect_items` | 7 | module | stop/8068 | module | 1 | stop/12514 | 820.8 |
| `fix_first_value` | 10 | module | stop/6813 | module | 1 | stop/14536 | 475.7 |
| `fix_insert_pos` | 5 | module | stop/2495 | **nothing** | 2 | length/16000, length/32000 | 763.5 |
| `fix_percentile` | 7 | module | stop/11371 | **nothing** | 2 | length/16000, length/32000 | 2099.5 |
| `fix_title_case` | 0 | **nothing** |  | **nothing** | 0 |  | 1343.6 |

Replies with no test in them, by `finish_reason`: `length` × 5
