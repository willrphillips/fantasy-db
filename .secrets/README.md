# .secrets (gitignored except the *.example files and this README)

| File | Who writes it | Used by |
|---|---|---|
| `yahoo.json` | Will types `client_id` + `client_secret` from the Yahoo dev app; `python -m nfl.yahoo_api auth` adds the tokens | `nfl/yahoo_api.py` |
| `yahoo_code.txt` | Will, once: the short code Yahoo shows after Agree. Deleted after use. | `python -m nfl.yahoo_api auth` |
| `yahoo_cookie.txt` | Will: the `Cookie:` header from a logged-in browser request | `nfl/yahoo_web.py` (projections only) |
| `serve-key` | `head -c 24 /dev/urandom \| base64 > serve-key` on the Mac; same value in `~/.nfl-key` on atlas for Edwin | `nfl/serve.py` |

All real files `chmod 600`. Never paste values into chat or a commit.
