## ticket-triage

6 cases, 6 pass, 0 fail, 1 flaky (3 runs each). Provider: `cmd python3 triage_model.py`.

| Case | Passed | Rate | Needs |
|---|---|---|---|
| `refund` | 3/3 | 100% ✅ | 100% |
| `locked-out` | 3/3 | 100% ✅ | 100% |
| `export-crash` | 3/3 | 100% ✅ | 100% |
| `praise` | 3/3 | 100% ✅ | 100% |
| `injection` | 3/3 | 100% ✅ | 100% |
| `ambiguous` | 2/3 | 67% ✅ flaky | 60% |

**ambiguous**

- json_enum category in ["billing"]: held in 2 of 3 runs. category is "other", not one of ["billing"]
