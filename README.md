# Raffle Promotion App — Visual + Module Upgrade

This package upgrades the Flask prototype around the supplied Raffle dashboard reference. The first interface uses the dark futuristic financial-entertainment layout: creator hero, live ticket pool, business meter, financial cards, winner tiers, campaign actions, quick actions, tutorials and mobile journey previews.

## Image architecture

The app no longer embeds creator images inside source code. Creator/campaign images use normal local static assets or uploads. The demo creator image is `static/sample_creator.jpg` and uploaded images are stored under `static/uploads/`. This keeps image replacement simple and prevents long image data from bloating application source files.

## Included module upgrades

- Mobile-first Raffle dashboard based on the supplied interface reference
- External video/audio campaign links
- Local currency formatting
- Wallet and ticket demo flow
- Server-side secure ticket number generation
- Economic/business meter with internally consistent figures
- Free promotional reward recording
- Multi-tier winner draw
- Winner feedback question with admin-configurable response options
- Withdrawal eligibility gate after winner feedback
- Winner social-proof notification foundation for non-winners
- Campaign slugs / web promotion URLs (`/c/<slug>`)
- Creator/campaign image upload without Base64
- Live preview with draggable overlay in the campaign creator
- Admin Control Room
- Demo/Live mode switch and payment-readiness guard
- Safe reset that preserves financial/audit history
- Tutorial & Help Center with admin-managed external tutorial URLs
- Tutorial click analytics foundation
- Health endpoint for deployment checks
- Render deployment files

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

The app listens on `PORT` (default `5000`).

## Render

`render.yaml` and `Procfile` are configured for a Python/Flask web service. `gunicorn` is now explicitly included in `requirements.txt`, fixing the original deployment blocker where Render could not find the Gunicorn executable.

For a prototype, SQLite is acceptable for local/demo testing. For real multi-user production, move the database to PostgreSQL or another managed database and use persistent/object storage for uploaded media.

## Important production boundary

Demo Mode is intended for functional testing. Real-money ticket sales and payouts require a verified payment provider, webhook verification, secure secrets, stronger authentication/authorization, fraud controls, persistent storage, compliance/legal review and jurisdiction-appropriate raffle/gaming rules before Live Mode is used commercially.

## Android later

The Flask web application can be wrapped or paired with a separate Android project later. Do not run Gradle directly against this Flask project.
