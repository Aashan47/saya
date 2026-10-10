"""Property tests for the heat index.

These cannot prove the NOAA coefficients were transcribed correctly. They check the
properties any correct implementation must have, which catches a typo or a sign error.
Verifying the constants themselves means comparing against NOAA's published chart, and
that is a human job listed in 05-HARDWARE.md and heat.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import heat  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILURES.append(name)


def main():
    print("unit conversion")
    check("0 C is 32 F", abs(heat.c_to_f(0) - 32.0) < 1e-9)
    check("100 C is 212 F", abs(heat.c_to_f(100) - 212.0) < 1e-9)
    check("round trip is stable", all(abs(heat.f_to_c(heat.c_to_f(c)) - c) < 1e-9 for c in range(-40, 60)))

    print("\nmonotonic in temperature at fixed humidity")
    for rh in (20, 50, 80):
        vals = [heat.heat_index_c(t, rh) for t in range(25, 50)]
        check("rising with T at {}% RH".format(rh), all(b > a for a, b in zip(vals, vals[1:])))

    print("\nmonotonic in humidity at fixed temperature")
    for t in (32, 38, 44):
        vals = [heat.heat_index_c(t, rh) for rh in range(20, 95, 5)]
        check("rising with RH at {} C".format(t), all(b > a for a, b in zip(vals, vals[1:])))

    print("\ndirection of the effect")
    check("humid heat feels hotter than it is", heat.heat_index_c(38, 70) > 38.0,
          heat.heat_index_c(38, 70))
    check("dry heat feels cooler than it is", heat.heat_index_c(40, 8) < 40.0,
          heat.heat_index_c(40, 8))
    check("humid is worse than dry at equal T",
          heat.heat_index_c(38, 80) > heat.heat_index_c(38, 20))

    print("\ncontinuity across the 80 F switch between the two forms")
    # NOAA's method switches between two different formulas, which do not meet exactly.
    # The step is a property of the published method, not of this implementation. What
    # is worth guarding is that it stays small: a large jump would mean a sign error.
    worst = max(abs(heat.heat_index_f(t / 10.0, 50) - heat.heat_index_f((t + 1) / 10.0, 50))
                for t in range(750, 850))
    check("boundary step stays small", worst < 1.0, "largest step {:.3f} F".format(worst))
    print("       (measured step {:.3f} F, inherent to NOAA's two-formula method)".format(worst))

    print("\nbands are ordered and labelled")
    order = ["safe", "caution", "extreme caution", "danger", "extreme danger"]
    seen = []
    for t in range(20, 60):
        label, _, _ = heat.band(t, 60)
        if not seen or seen[-1] != label:
            seen.append(label)
    check("bands only escalate", seen == [s for s in order if s in seen], seen)

    print("\nabove the chart: the danger is still reported, the figure is marked")
    good = heat.assess(32, 50)
    check("an ordinary hot reading is exact", good["heat_index_is_extrapolated"] is False, good)
    check("and quotes a figure", good["heat_index_c"] is not None, good)

    monsoon = heat.assess(38, 65)
    check("a real monsoon reading is NOT silenced", monsoon["band"] != "unknown", monsoon)
    check("it is classed as dangerous", monsoon["band"] in ("danger", "extreme danger"), monsoon)
    check("its figure is marked extrapolated", monsoon["heat_index_is_extrapolated"] is True, monsoon)
    check("it states a floor instead", monsoon["heat_index_floor_c"] is not None, monsoon)

    extreme = heat.assess(45, 80)
    check("an extreme reading still escalates", extreme["band"] == "extreme danger", extreme)
    check("and is marked extrapolated", extreme["heat_index_is_extrapolated"] is True, extreme)

    cool = heat.assess(18, 50)
    check("a cool reading is exact", cool["heat_index_is_extrapolated"] is False, cool)
    check("and is safe", cool["band"] == "safe", cool)

    print("\nreference table, EYEBALL THIS AGAINST NOAA BEFORE QUOTING ANY NUMBER")
    print("     RH:  " + "".join("{:>8}".format(str(r) + "%") for r in (20, 40, 60, 80)))
    for t in (30, 35, 40, 45):
        row = "".join(
            "{:>8}".format(("~{:.0f}+" if heat.assess(t, r)["heat_index_is_extrapolated"]
                            else "{:.1f}").format(heat.assess(t, r)["heat_index_c"]))
            for r in (20, 40, 60, 80))
        print("  {:>2} C: {}".format(t, row))

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all property checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
