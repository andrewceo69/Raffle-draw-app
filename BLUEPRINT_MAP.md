# Blueprint → implementation map

| Blueprint area | Upgrade implementation |
|---|---|
| External media links | `campaigns.media_url` + outbound links |
| Creator image / branding | local static image + campaign image upload |
| Image architecture | local static assets + upload storage; no embedded image data in source |
| Local currency | `CURRENCIES` + user/campaign currency |
| Wallet | `users.wallet_minor` + `ledger` |
| Paid tickets | `tickets` + `/campaign/<id>/buy` |
| Free promotional reward | `rewards` + `/campaign/<id>/reward` |
| Winner tiers | `winner_tiers` + secure server-side draw |
| Economic threshold | `threshold` + `economic_target_minor` |
| Campaign target tickets | `target_tickets` |
| Business meter | `campaign_stats()` |
| Winning / Watch / Losing | economic position calculation |
| Winner feedback | `winners.feedback` + `/winner/<id>` |
| Configurable winner answers | `settings.winner_response_options` |
| Withdrawal gate | `winners.withdrawal_eligible` + `/winner/<id>/withdraw` |
| Winner social proof | `notifications` created for eligible non-winners |
| Campaign URLs | `campaigns.slug` + `/c/<slug>` |
| Image uploads | `/campaign/<id>/upload-image` + `static/uploads/` |
| Live preview | draggable preview in `new.html` |
| Demo/Live mode | `settings.system_mode` + `payment_ready` guard |
| Tutorial center | `tutorials` + admin tutorial manager |
| Tutorial URL viewing | `/tutorial/<id>/view` |
| Tutorial analytics | `tutorial_views` |
| Notifications | `notifications` foundation |
| Auto Reset | `/admin/reset` |
| Audit | `audit` |
| Admin Control Room | `/admin` |
| Render deployment | `render.yaml`, `Procfile`, `gunicorn` dependency |
| Mobile-first look | responsive CSS |
| Money-conscious visual language | localized financial cards and meter |

## Known production upgrades still required

The package is a functional/demo foundation, not a production real-money gambling/payment service. Production requires managed PostgreSQL, persistent object storage, real authentication and RBAC, verified payment/payout webhooks, stronger audit/draw infrastructure, fraud controls, rate limiting, observability, secrets management, backups, compliance/legal review and jurisdiction-specific rules.
