# Condensation risk

The **Condensation risk** card tells the crew how close the air is to making things wet. It shows
the site **air temperature**, the **dew point** and the **margin** between them, with a plain word:

| Word | Margin (air minus dew point) | What it means |
| --- | --- | --- |
| Dry | 8 °C or more | Little risk of condensation |
| Low risk | 4 to 8 °C | Cold kit may mist if it is much colder than the air |
| Near saturation | 1 to 4 °C | Air near saturation: cold kit may mist |
| Condensing | under 1 °C | Air at saturation: cold surfaces will be wet |

The word is always shown as well as the colour. If there is no temperature or humidity reading, the
card says **No reading**. It never guesses.

It is not on any dashboard by default. Your tech adds it under **Admin, User dashboards, Edit cards**.

Back to [Dashboard cards](README.md).

## Kit that has been somewhere cold

Kit colder than the dew point will fog up. This happens when it comes in from a cold truck, for
example. Type the kit's temperature into **Kit temperature (°C)** and the card says whether that
kit is below the dew point, close to it (within 2 °C) or above it. The box is on tablets and phones
only. What you type stays in that browser. It is not sent to Stagewatch or saved with the show.

## Good to know

- **Guidance, not a guarantee.** Surfaces, airflow and how long kit has had to warm up all change
  what really happens. There is no alarm, no sound and no marker.
- The figures come from the site readings: the average of your environment sensors. Readings from
  Equipment sensors (amp racks and similar) are left out.
- The card needs a temperature and a humidity sensor. See [Site readings](env-tiles.md).
- The small print is under **About these figures**. The wall screen shows only the word and the
  three figures.
