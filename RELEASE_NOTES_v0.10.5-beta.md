# OwO Boss Helper v0.10.5 Beta

This release adds Neon weapon dex queues and expands boss-ticket lookup.

## Neon weapon scanner

The bot now watches public NeonUtil weapon inventory pages from Neon bot ID `851436490415931422`. When a page contains weapons where Neon shows **M** / max possible quality, OwO Boss Helper records those weapon IDs under the Discord user shown in Neon's title.

The scanner:

- parses Neon weapon and saved-weapon pages;
- stores queues per owner Discord user ID and weapon ID;
- deduplicates repeated pages and back/forward scrolling;
- saves current quality, max-estimated quality, raw row text, recognized filters, and emoji IDs;
- adds a visible `🧾` reaction after a page has been scanned;
- does not rely on one stable emoji ID per weapon or passive.

## Dex guide

New commands:

```text
H weapons / HW
HWD
H dex
H weapon dex
H weapondex
HW dex
HWD dagger
H dex dagger mtap sg
H weapon stats
H weapon clear
```

`H weapons / HW` explains the Neon setup flow based on Pencilvester's notes: run `nw inv public`, run `ww`, click Neon's reaction, then page through the inventory.

`HWD` / `H dex` shows guided `ww <weapon_id>` commands for queued weapons. The guide includes a five-second step guard so users do not advance too quickly while waiting for OwO and Neon responses.

When a member sends `ww <weapon_id>` and Neon replies with a blueprint such as `sword 31,7 mtap 77`, the helper marks that weapon saved and learns any available weapon/passive context.

## Filter-aware without fragile emoji assumptions

Neon uses many emoji IDs for the same weapon/passive across rarity and frame variants. This release therefore uses a hybrid strategy:

1. Neon filter headers, such as `pdagger`, `manatap`, and `safeguard`.
2. Blueprint replies after `ww <weapon_id>`.
3. Known aliases from Neon help text and OwO weapon/passive names.
4. Raw unknown rows remain in the unfiltered dex queue until more context is learned.

## Multi-user HBT

`HBT` can now look up multiple tracked members at once:

```text
HBT hassaan pencil krish
HBT @user1 @user2 @user3
HBT "Candy Call Me Eyes" Oldie
```

Ambiguous terms list candidates instead of guessing.

## Storage

A new local SQLite file, `neon_weapons.db`, stores scanned Neon weapon queue data. Users can clear their own scanned entries with `H weapon clear`. No manual SQL migration or dependency installation is required.

## Version

```text
0.10.5-beta
```


### Guided dex sessions

- Added a **Start dexing session** button to the Neon weapon dex queue.
- The bot now posts one `ww <weapon_id>` command at a time, advances after the user sends the command, and waits about five seconds before showing the next prompt.
- Added `H stop` to pause an active dex session.
- Clarified `HW stats` so scanned, need-dex, saved/exact/dexed, and no-action counts are easier to understand.

### Mobile-friendly guided dex prompts

- Guided Neon weapon dex sessions now use plain text prompts instead of embed prompts.
- Added `HS` and `Hstop` aliases for pausing a guided dex session.
- `H help` now includes the Neon weapon dex section with the guided-session commands and stop aliases.
- The Neon setup guide now refers to Neon's name/setup reaction when explaining the initial weapon upload flow.
