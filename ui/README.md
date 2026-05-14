# Supply Chain Monitor

Real-time monitoring dashboard for the 3D printer supply chain simulator.
Single-page React app that watches the three FastAPI services running locally:

- Provider — `http://localhost:8001`
- Manufacturer — `http://localhost:8002`
- Retailer — `http://localhost:8003`

The Vite dev server proxies the three apps under `/p`, `/m`, `/r` so the
browser never has to deal with CORS (the backends do not enable
`CORSMiddleware`). The Swagger links open the apps' direct hosts in a new
tab, which works regardless of CORS.

## Run

From this folder:

```bash
npm install
npm run dev
```

Then open http://localhost:5173. Make sure the three FastAPI apps are
running (e.g. `provider-cli serve`, `manufacturer-cli serve`,
`retailer-cli serve`) before hitting refresh.

## Features

- Header with current simulation day (max across the three apps) and a
  manual refresh button.
- Status pills (Provider / Manufacturer / Retailer): green dot when the
  app is up, red dot when it's offline. Click to open Swagger.
- Day timeline showing all three clocks; flags desynchronization.
- 3-column main grid with stock, orders, and (for the manufacturer) a
  capacity gauge plus parts inventory with `committed` / `in_transit` /
  `deficit` annotations.
- Supply-chain flow diagram with animated dashed lines for active edges.
- Right-hand event feed (max 20), synthesized from order lifecycle data
  and price catalogs across the three apps.
- Auto-refresh: 10 s / 30 s / off.
- Errors are isolated per app — one app being down doesn't crash the UI.

## Stack

- React 18 + hooks
- Vite 5 (dev server + proxy)
- Tailwind CSS v4 (`@tailwindcss/vite`)
- Recharts (radial gauge)
