# DirectOut GLOBCON

Live level meters from one DirectOut GLOBCON controller. It answers "is the signal there, and roughly how loud is each channel?".

This is a convenience view. It is not a metering standard, and it is not a way to run the mix. Stagewatch only listens to GLOBCON. It never moves a fader, mutes, solos, changes a layer or presses a button.

Part of the [dashboard cards](README.md). Half size is available. The card stays hidden until GLOBCON has answered. The address, password, controller and channel group are set up in Admin: see [DirectOut GLOBCON](../integrations/globcon.md).

## What you see

The card title is always **DirectOut GLOBCON**. Under it is a subtitle such as **Controller 1 - Layer 1 - Channels 1-16**. It names the controller and its current layer as GLOBCON reports them right now. If someone changes the layer on the controller, the subtitle and the channel names change with it.

Each channel sits in its own box, all in one row, whether you show 4, 8 or 16. Every meter has the same width. They only get narrower when the card is too narrow. A phone shows 16 channels in four rows. Each box has:

- The channel number, at the top.
- The channel's name as GLOBCON gives it, to the left of the meter and reading upwards.
- A tall bar that fills as the level rises. The scale runs from -72 dB at the bottom to 0 dB at the top.
- The level in dB underneath.

On a half-width tablet card with all 16 channels there is no room beside the bar, so the name sits above it there.

The channel groups are 1-4, 5-8, 1-8, 9-12, 13-16, 9-16 and 1-16. These are the channel positions along the controller's faders on the layer it is showing now. Different screens can show different groups.

## How to read it

- **The levels are GLOBCON's own numbers in dB.** Stagewatch does not average, smooth, hold or convert them. It does not say whether they are peak or RMS, because GLOBCON does not say.
- **An empty bar and a dash** mean there is no level to show. GLOBCON reports "no signal", the channel has no level meter (such as the USB channels on some set-ups), or no reading has arrived yet. It is never shown as zero.
- **The bar changes colour at high levels:** amber from about -9 dB and orange from about -3 dB. This is a display guide only. It is not a statement about clipping or headroom. The colour is the only sign, so do not rely on it alone.
- **MUTED** on a bar, with an orange outline and a dimmed bar, means the channel is muted on the GLOBCON controller. Stagewatch only reads this. It can never set a mute.

## What the card says

| Badge | What it means |
|---|---|
| **● LIVE** | Levels are arriving. |
| **▲ FROZEN** | No new levels for a few seconds, or GLOBCON is not connected. The last levels stay on screen, dimmed grey, with the figures struck through. The note says how long ago they arrived. Nothing is shown as zero and nothing is filled in afterwards. |
| **▲ PASSWORD NEEDED** | GLOBCON wants a password for this controller. Ask your tech to enter it in Admin. |
| **… WAITING** | The card has not heard from GLOBCON yet. |

When GLOBCON is not connected, the card shows **Offline** in its top-right corner (click it for what to check) and nothing else.

When GLOBCON goes quiet or away, Stagewatch raises a quiet notice in the alarm bar. It never makes a sound. The notice clears when GLOBCON is back. Nothing is recorded: the card is live only, so there is no history and no marker.

## Online, Offline or Error

The card header has a small mark beside the DirectOut logo: **Online** (green ●), **Offline** (amber ✕) or **Error** (red !). Tap it to see how long since the last data and what to try, such as checking that GLOBCON is running and the Windows firewall allows it. On a wall screen it only shows when GLOBCON is not online. The marks are explained in [the dashboard header](sensors.md#the-dashboard-header).

## Who can see it

Dashboards have no password. Anyone on the show network can see the channel names and levels on a screen that has this card.

## Tested on real kit

Names, layers and live level meters have been checked on one real GLOBCON. The connection was built from GLOBCON's own web page and checked against one recording of that page talking to a real GLOBCON. Please check the levels against GLOBCON's own screen before you rely on them.

Which scale GLOBCON uses for each type of channel is not known yet, so do not compare a bar with a meter on another device.

Related: [Troubleshooting](../troubleshooting.md).
