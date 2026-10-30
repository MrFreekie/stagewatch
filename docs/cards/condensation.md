# Condensation risk

The **Condensation risk** card tells the crew how close the air is to making things wet. It shows
the site **air temperature**, the **dew point** and the **margin** between them, with a plain word:

| Word | Margin (air minus dew point) | What it means |
| --- | --- | --- |
| Condensing | under 1 °C | Air at saturation: cold surfaces will be wet |
| Near saturation | 1 to 4 °C | Kit more than the margin colder than the air will mist |
| Moderate | 4 to 10 °C | Kit more than the margin colder than the air will mist |
| Low risk | 10 °C or more | Low risk for kit at room temperature |

Each line under the word gives the actual number, for example "Kit more than 3.0 °C colder than the air will mist".

The word is always shown as well as the colour. If there is no temperature or humidity reading, the
card says **No reading**. It never guesses.

It is not on any dashboard by default. Your tech adds it under **Admin, User dashboards, Edit cards**.

Back to [Dashboard cards](README.md).

## Kit that has been somewhere cold

Kit colder than the dew point will fog up. This happens when it comes in from a cold truck, for
example. Type the kit's temperature into **Kit temperature (°C)** and the card says whether that
kit is below the dew point, close to it (within 3 °C) or above it. Below 0 °C it compares with the frost point instead, when that is higher than the dew point. An infrared thermometer reads low on shiny metal, so allow for that. The box is on tablets and phones
only. What you type stays in that browser. It is not sent to Stagewatch or saved with the show.

## Good to know

- **Guidance, not a guarantee.** Surfaces, airflow and how long kit has had to warm up all change
  what really happens. There is no alarm, no sound and no marker.
- The figures come from the site readings: the average of your environment sensors. Readings from
  Equipment sensors (amp racks and similar) are left out.
- The card needs a temperature and a humidity sensor. See [Site readings](env-tiles.md).
- **Not yet tested on real kit.** The maths is checked, but the card has not been compared with a
  real cold-kit situation. Treat the word as a hint.
- The small print is under **About these figures**. The wall screen shows only the word and the
  three figures.
