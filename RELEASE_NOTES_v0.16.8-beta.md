# v0.16.8-beta — Extended Knowledge-Switch Simulations

`H XP` now shows the benefit of 15% Knowledge switching from streak zero at two meaningful checkpoints: 1,000 and 10,000 battles. Every row explicitly reports extra XP per pet for switching every 10, 50, 100, 500, or 1,000 wins, plus the number of switches made through 10,000.

The longer result is calculated milestone by milestone. It is not the first 1,000 battles multiplied by ten, because OwO's milestone bonus grows with the square root of the streak.

`/xp-calc` now has two distinct streak inputs:

- **Current streak** determines the average battle XP used by the level estimate.
- **Maximum streak** determines the streak-zero-to-maximum horizon used by the five switching comparisons. It defaults to 10,000 and must be at least the current streak.

The slash calculator also makes its team assumption explicit: the entered highest teammate remains at that level. Members who want all three pets to level together can use the full forecaster opened from Team XP.

No database migration, dependency update, or new Discord permission is required. The slash-command schema gains the optional `maximum_streak` parameter and will synchronize automatically during startup.
