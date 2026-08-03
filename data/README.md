# Checked-in game reference data

These JSON files contain public OwO game reference facts used by the helper:

- `special_animals.json`: the current finite Special/event animal catalog,
  including aliases, rank, event, rarity, global caught total when published,
  six base stats, and the matching local application-emoji asset.
- `game_reference.json`: public weapon, passive, weapon-quality, and animal-rank
  reference facts retained for the guide system and future features.

The source page and retrieval timestamp are recorded inside each file. Refreshes
are performed manually with `scripts/sync_wiki_game_data.py`, reviewed as a normal
source diff, and tested before release; production does not scrape the wiki at
startup.

Public game facts are not user activity. Runtime facts learned from strict
official OwO Dex responses live separately in the ignored `animal_dex.db` file.
