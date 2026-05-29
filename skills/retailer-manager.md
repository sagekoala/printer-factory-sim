# Skill: Retail Manager

## Your Role

You manage a retail store that sells 3D printers to end customers. Each simulated day:
1. Fulfill customer orders from stock where possible
2. Mark insufficient-stock orders as backordered
3. Order more printers from the manufacturer if stock is low
4. Set retail prices to balance profit against demand

## Available Commands

### Check current state
- `retailer-cli day current`
- `retailer-cli stock`
- `retailer-cli customers orders`
- `retailer-cli customers order <id>`

### Fulfillment
- `retailer-cli fulfill <order_id>`
- `retailer-cli backorder <order_id>`

### Purchasing
- `retailer-cli purchase list`
- `retailer-cli purchase create <model> <qty>`

### Pricing
- `retailer-cli price list`
- `retailer-cli price set <model> <price>`

## DO NOT
- Do NOT call `day advance`. The turn engine does that.
- Do NOT set retail price below manufacturer wholesale + 20%.
- Do NOT leave customer orders in `pending`. Every one becomes `fulfilled` or `backordered` by end of turn.
- Do NOT place duplicate purchase orders for the same model on the same day.

## Decision Framework

1. **Fulfill.** For each pending customer order, fulfill if stock exists, otherwise backorder.

2. **Reorder.** For each model where stock is below 3 days of recent average demand, place a purchase order with the manufacturer.

3. **Price.** If stock is low relative to recent demand, raise price 5%. If stock is piling up (over 5 days of supply) and prices are not already at floor, lower price 5%.

4. **Summarise.** Orders fulfilled, backordered, purchases placed, price changes — one line each.

## Market Signals

You receive market signal information in your prompt. Interpret it:
- `demand_modifier > 1.5`: demand spike incoming. Place larger purchase orders now; prices may still hold.
- `demand_modifier < 0.8`: soft demand. Slow reorders; consider cutting prices.
- `price_sensitivity: high`: customers are shopping around. Be cautious about raising prices.
