# v0.16.7-beta — Persistent XP Model and Guide Attribution

The settings submitted in the full-team XP calculator are now the member's saved model. Battles and fixed XP per day, XP per hunt, tie and loss rates, maximum streak, Knowledge quality, and forecast line count survive bot restarts and remain available across servers until that member changes them.

Every future Team XP panel now compares three estimates:

- **Normal**, the unchanged reference calculation without the saved model;
- **Your model**, which applies all saved activity and outcome settings; and
- **Model + Max Knowledge**, which applies that same model with the saved Knowledge quality.

The full forecaster opens with the same saved values, and modeled battle counts include an estimated day count whenever battles per day is configured.

The bot's Pet XP tools were inspired by Heather's original [Pet EXP guide](https://owo.bwep.net/exp/). The named credit appears in `H XP` / `H EXP` and Team XP tools, alongside the community-sheet acknowledgement, without adding extra text to the calculator form.

This release adds one automatic table to the existing XP SQLite database. It requires no dependency update, slash-command schema change, or new Discord permission.
