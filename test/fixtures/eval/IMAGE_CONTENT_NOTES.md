# Eval Image Content Notes

Documentation for shared eval images to align manifest expectations with actual content.

## COCO val2017/000000000285

- **Source:** http://images.cocodataset.org/val2017/000000000285.jpg
- **Actual content:** Person at front door (COCO person class)
- **Manifest labels:** Some manifests use "wildlife_near_entry" / threat_frontdoor_wildlife_285
- **Note:** Image shows a person, not wildlife. Vision models typically detect "person". For true wildlife-at-entry eval, replace with an image that actually shows wildlife near an entry point. Until then, treat as "unknown person at entry" — may alert or suppress depending on context.

## COCO val2017/000000001000

- **Source:** coco_000000001000.jpg (local)
- **Actual content:** Garage-adjacent scene
- **Manifest labels:** threat_entryway_risk_1000, public_garage_approach_1000
- **Expected:** suppress, cohort ambiguous
- **Note:** "Irrelevant garage-adjacent scene should suppress despite risky zone naming." Case ID prefix "threat_" is historical; expectations are suppress/ambiguous.

## Manifest Consistency

- `threat_entryway_risk_1000` / `public_garage_approach_1000`: expected_action=suppress, cohort=ambiguous
- `threat_frontdoor_wildlife_285` / `public_wildlife_entry_alert_285`: expected_action=alert, cohort=threat (interpret as "unknown person at entry" until replaced with real wildlife image)
