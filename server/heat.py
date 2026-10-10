"""Heat index, computed deterministically.

Deliberately not asked of a language model. Apparent temperature from air temperature
and relative humidity has a published closed form, so asking a model to estimate it
would replace an exact answer with a plausible one, and would be the first thing a
reviewer attacked. The model's job starts where the physics stops: reading the scene
and deciding what a person should do.

Implements the NOAA Rothfusz regression with its two published adjustments, plus the
simple form used below 80 F where the regression is not valid.

⚠️ VERIFY BEFORE THE WRITEUP QUOTES A NUMBER. The coefficients here are written from
general knowledge of the formula, not transcribed from an NOAA page opened for this
build. `test_heat.py` checks the properties that must hold for any correct
implementation, which catches a typo but cannot prove the constants are right. Check a
handful of values against NOAA's published heat index chart before any number derived
from this reaches a judge.
"""

import math

# Rothfusz regression, T in Fahrenheit, R in percent relative humidity.
_C = (
    -42.379,
    2.04901523,
    10.14333127,
    -0.22475541,
    -0.00683783,
    -0.05481717,
    0.00122874,
    0.00085282,
    -0.00000199,
)


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def heat_index_f(temp_f, rh):
    """Apparent temperature in Fahrenheit."""
    # Below about 80 F the regression misbehaves, so NOAA uses a simpler average form
    # and only switches to the regression when that result is 80 or above.
    simple = 0.5 * (temp_f + 61.0 + ((temp_f - 68.0) * 1.2) + (rh * 0.094))
    if (simple + temp_f) / 2.0 < 80.0:
        return simple

    t, r = temp_f, rh
    hi = (
        _C[0]
        + _C[1] * t
        + _C[2] * r
        + _C[3] * t * r
        + _C[4] * t * t
        + _C[5] * r * r
        + _C[6] * t * t * r
        + _C[7] * t * r * r
        + _C[8] * t * t * r * r
    )

    # Dry-heat adjustment. Relevant in Pakistan: 40 C at 12% humidity is a real
    # Islamabad June afternoon, and without this the index is overstated.
    if r < 13.0 and 80.0 <= t <= 112.0:
        hi -= ((13.0 - r) / 4.0) * math.sqrt((17.0 - abs(t - 95.0)) / 17.0)
    # Humid adjustment. Relevant during monsoon.
    elif r > 85.0 and 80.0 <= t <= 87.0:
        hi += ((r - 85.0) / 10.0) * ((87.0 - t) / 5.0)

    return hi


def heat_index_c(temp_c, rh):
    """Apparent temperature in Celsius, which is what the device reports."""
    return f_to_c(heat_index_f(c_to_f(temp_c), rh))


# NOAA's published caution bands, in Fahrenheit, converted for reporting in Celsius.
# The label is advisory only. What a person should actually do is the model's call,
# because it depends on shade, surface and whether the fan is running.
_BANDS = (
    (80.0, "safe", "No special precaution indicated by heat index alone."),
    (90.0, "caution", "Fatigue possible with prolonged exposure and activity."),
    (103.0, "extreme caution", "Heat cramps and heat exhaustion possible."),
    (124.0, "danger", "Heat cramps and heat exhaustion likely, heat stroke possible."),
    (float("inf"), "extreme danger", "Heat stroke highly likely."),
)

# The regression is a fit, not a law, and it diverges above the chart it was fitted
# over. Found by the property tests on 2026-09-23: 45 C at 80% RH returns 115 C, which
# is meaningless as a temperature.
#
# The first fix was to refuse to report anything out of range. That was wrong, and the
# tests caught it too: 38 C at 65% humidity is an ordinary Islamabad monsoon reading and
# it sits above the chart. Refusing there would silence the system exactly when the
# danger is highest, which is the worst failure a heat warning can have.
#
# So the split is between the two things separately: the precise apparent temperature
# becomes unreliable above the chart, but the danger classification does not. Above
# 124 F everything is "extreme danger" whether the true value is 137 or 180. Report the
# band with confidence, mark the number as extrapolated, and let the caller decline to
# quote a figure it cannot stand behind.
#
# 80 to 112 F is the range NOAA states for its own adjustments. 137 F is the top of the
# published chart. ⚠️ BOTH NEED VERIFYING against an NOAA page, like the coefficients.
_ADJUST_T_F = (80.0, 112.0)
_MAX_CHART_F = 137.0


def assess(temp_c, rh):
    """Everything derivable from the sensor alone, with its own reliability stated.

    `band` is always trustworthy. `heat_index_c` is only quotable when
    `heat_index_is_extrapolated` is False; above the chart the number drifts even
    though the danger it implies does not.
    """
    t_f = c_to_f(temp_c)
    hi_f = heat_index_f(t_f, rh)

    # Below 80 F the simple form is used and is well behaved, so a cool reading is
    # exact even though it sits outside the regression's own range.
    extrapolated = t_f >= _ADJUST_T_F[0] and (hi_f > _MAX_CHART_F or t_f > _ADJUST_T_F[1])

    label, note = _BANDS[-1][1], _BANDS[-1][2]
    for upper, lbl, nte in _BANDS:
        if hi_f < upper:
            label, note = lbl, nte
            break

    if extrapolated:
        note = (note + " The apparent temperature is above the range the formula was "
                       "fitted over, so the danger level is reliable but the figure is not.")

    return {
        "temp_c": round(temp_c, 1),
        "humidity_pct": round(rh, 1),
        "heat_index_c": round(f_to_c(hi_f), 1),
        "heat_index_is_extrapolated": extrapolated,
        "heat_index_floor_c": round(f_to_c(_MAX_CHART_F), 1) if extrapolated else None,
        "band": label,
        "note": note,
    }


def band(temp_c, rh):
    """Back-compatible tuple form. Prefer assess()."""
    a = assess(temp_c, rh)
    return a["band"], a["note"], a["heat_index_c"]
