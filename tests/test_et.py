"""Tests for hkrd.derive.et — the regression net for the ET rewrite."""

import numpy as np
import pandas as pd
import pytest

from hkrd.derive import et


class TestParseLbw:
    """66% of stored lbw values are fractional. pd.to_numeric drops them all."""

    @pytest.mark.parametrize("raw,expected", [
        ("1/2", 0.5), ("3/4", 0.75), ("1-1/2", 1.5), ("3-1/4", 3.25),
        ("7-3/4", 7.75), ("12", 12.0), ("101-1/4", 101.25),
        ("SHD", 0.1), ("HD", 0.2), ("NK", 0.3),
    ])
    def test_parses_hkjc_formats(self, raw, expected):
        assert et.parse_lbw(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["---", "-", "", None, "nan"])
    def test_winner_and_blank_are_nan(self, raw):
        assert np.isnan(et.parse_lbw(raw))

    def test_beats_to_numeric_on_real_formats(self):
        vals = ["1/2", "3-1/4", "1-1/2", "3/4", "12"]
        ours = [et.parse_lbw(v) for v in vals]
        theirs = pd.to_numeric(pd.Series(vals), errors="coerce")
        assert sum(np.isnan(ours)) == 0
        assert theirs.isna().sum() == 4  # the bug this test exists to prevent


class TestParseFinishTime:
    @pytest.mark.parametrize("raw,expected", [
        ("109.23", 109.23), ("1:49.23", 109.23), ("2:00.50", 120.50), (70.04, 70.04),
    ])
    def test_formats(self, raw, expected):
        assert et.parse_finish_time(raw) == pytest.approx(expected)


class TestSurfaceNormalisation:
    def test_awt_going_never_labelled_turf(self):
        """The v4 Ultra table had going_group='AWT-Standard' with track_type='Turf'."""
        course, surface = et.normalise_surface("AWT", "Turf", "AWT-Standard")
        assert surface == "AWT"

    def test_awt_course_detected(self):
        assert et.normalise_surface("AWT", "All Weather Track", "GOOD")[1] == "AWT"

    def test_turf_unaffected(self):
        course, surface = et.normalise_surface("A", "Turf", "GOOD")
        assert (course, surface) == ("A", "Turf")


class TestWeightBand:
    @pytest.mark.parametrize("w,band", [
        (110, "<=112"), (113, "113-115"), (115, "113-115"),
        (116, "116-118"), (140, ">=137"),
    ])
    def test_bands(self, w, band):
        assert et.weight_band(w) == band


@pytest.fixture
def synthetic_runs():
    """400 runs where 1200m Turf has a true par of 70.0s."""
    rng = np.random.default_rng(0)
    rows = []
    for i in range(400):
        rows.append({
            "race_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i // 10),
            "race_no": (i % 10) + 1,
            "distance": 1200,
            "going": "GOOD",
            "race_course": "A",
            "track_type": "Turf",
            "actual_weight": 120,
            "race_class": "4",
            "finish_time": 70.0 + rng.normal(0, 0.9),
            "lbw": "---" if i % 12 == 0 else "1-1/2",
        })
    return pd.DataFrame(rows)


class TestReferences:
    def test_builds_all_levels(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        assert set(refs.tables) == {"dist", "ultra", "coarse", "fine"}
        assert refs.n_runs == 400

    def test_par_recovers_truth(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        par = refs.tables["dist"].iloc[0]["par"]
        assert par == pytest.approx(70.0, abs=0.2)

    def test_too_few_runs_raises(self):
        with pytest.raises(et.ETError, match="too few"):
            et.build_references(
                pd.DataFrame({
                    "race_date": [pd.Timestamp("2026-01-01")], "race_no": [1],
                    "distance": [1200], "going": ["GOOD"], "finish_time": [70.0],
                    "lbw": ["---"],
                }),
                window_months=None,
            )


class TestShrinkage:
    def test_thin_cell_pulled_toward_coarse(self, synthetic_runs):
        """A 2-run cell must not override a 400-run one — the v4 bug."""
        outlier = synthetic_runs.iloc[:2].copy()
        outlier["race_class"] = "1"
        outlier["finish_time"] = 60.0          # absurdly fast
        data = pd.concat([synthetic_runs, outlier], ignore_index=True)

        refs = et.build_references(data, window_months=None, min_runs=100)
        out = et.expected_time(outlier, refs)

        # unshrunk would sit at 60.0; shrunk must stay near the 70s population
        assert out["et"].iloc[0] > 66.0
        assert out["et_shrunk"].iloc[0]

    def test_shrinkage_off_reproduces_raw_cell(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, shrinkage_k=0.0, min_runs=100)
        out = et.expected_time(synthetic_runs, refs)
        assert out["et"].std() < 1e-6  # all rows share one cell


class TestFailsLoudly:
    def test_unknown_distance_raises_not_nan(self, synthetic_runs):
        """The whole point of the rewrite: missing values raise, never vanish."""
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        alien = synthetic_runs.iloc[:1].copy()
        alien["distance"] = 9999
        with pytest.raises(et.ETError, match="no expected time|-level par matched"):
            et.expected_time(alien, refs)

    def test_missing_columns_raise(self):
        with pytest.raises(et.ETError, match="missing required columns"):
            et.prepare_runs(pd.DataFrame({"race_date": [], "race_no": []}))


class TestSpeedFigure:
    def test_faster_than_par_is_positive(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        fast = synthetic_runs.iloc[:1].copy()
        fast["finish_time"] = 68.0
        out = et.speed_figure(fast, refs)
        assert out["sec_vs_par"].iloc[0] > 0
        assert out["len_vs_par"].iloc[0] > 0
        assert out["time_figure"].iloc[0] > 100
        # A field of one is its own middle: the headline figure is 100 however
        # fast the time, because it is read against the field and not the par.
        assert out["figure"].iloc[0] == 100.0

    def test_race_relative_centres_on_zero(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        out = et.speed_figure(synthetic_runs, refs)
        med = out.groupby(["race_date", "race_no"])["sec_vs_race"].median()
        assert med.abs().max() < 1e-9

    def test_every_row_carries_confidence(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        out = et.speed_figure(synthetic_runs, refs)
        assert out["confidence"].notna().all()
        assert (out["derive_version"] == et.DERIVE_VERSION).all()

    def test_format_never_bare_number(self, synthetic_runs):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        out = et.speed_figure(synthetic_runs, refs)
        s = et.format_figure(out.iloc[0])
        assert "mid-field" in s and "par" in s and "n_eff=" in s


class TestFieldFigure:
    """The headline figure from et-v6.0: 100 + lengths ahead of mid-field."""

    def test_one_point_is_one_length_inside_six(self):
        x = np.array([-6.0, -3.5, -0.25, 0.0, 0.5, 2.0, 6.0])
        assert et.field_figure(x) == pytest.approx(100 + x)

    def test_bends_toward_the_limit_and_never_reaches_it(self):
        big = et.field_figure(np.array([7.0, 10.0, 20.0, 40.0, -40.0]))
        assert (np.abs(big - 100) < et.FIELD_LIMIT).all()
        assert big[3] > 109.9 and big[4] < 90.1

    def test_strictly_increasing_so_a_faster_time_never_rates_worse(self):
        """AGENTS.md: a faster time must always produce a better figure."""
        x = np.linspace(-30, 30, 6001)
        assert (np.diff(et.field_figure(x)) > 0).all()

    def test_smooth_where_the_bend_starts(self):
        """No kink at six lengths: either side of it a length is still a point."""
        eps = 1e-4
        f = et.field_figure
        left = (f(et.FIELD_LINEAR) - f(et.FIELD_LINEAR - eps)) / eps
        right = (f(et.FIELD_LINEAR + eps) - f(et.FIELD_LINEAR)) / eps
        assert left == pytest.approx(1.0, abs=1e-3)
        assert right == pytest.approx(1.0, abs=1e-3)

    def test_nan_stays_nan(self):
        assert np.isnan(et.field_figure(np.nan))


@pytest.fixture
def one_field():
    """Twelve runners in ONE race, beaten margins from a nose to 25 lengths.

    `synthetic_runs` puts a single runner in every race, so every field figure
    in it is 100 and a field test run on it would pass without testing anything.
    """
    margins = [0, 0.5, 1.25, 2.0, 2.75, 3.5, 4.0, 5.25, 6.5, 8.0, 11.0, 25.0]
    return pd.DataFrame([{
        "race_date": pd.Timestamp("2026-03-01"), "race_no": 1, "distance": 1200,
        "going": "GOOD", "race_course": "A", "track_type": "Turf",
        "actual_weight": 120, "race_class": "4",
        "finish_time": 69.5 + m * et.DEFAULT_SEC_PER_LENGTH,
        "lbw": "---" if m == 0 else "1", "horse_name": f"H{i}",
    } for i, m in enumerate(margins)])


class TestFieldFigureInARace:
    def _out(self, synthetic_runs, field):
        refs = et.build_references(synthetic_runs, window_months=None, min_runs=100)
        return et.speed_figure(field, refs).sort_values("finish_time")

    def test_middle_of_the_field_reads_100(self, synthetic_runs, one_field):
        out = self._out(synthetic_runs, one_field)
        assert out["figure"].median() == pytest.approx(100.0, abs=0.051)

    def test_figure_is_len_vs_race_on_the_100_scale(self, synthetic_runs, one_field):
        out = self._out(synthetic_runs, one_field)
        expect = np.round(et.field_figure(out["len_vs_race"].to_numpy()), 1)
        assert out["figure"].to_numpy() == pytest.approx(expect)

    def test_faster_is_always_better_and_the_tail_is_held(self, synthetic_runs, one_field):
        out = self._out(synthetic_runs, one_field)
        figs = out["figure"].to_numpy()
        assert (np.diff(figs) < 0).all()             # in finishing order, no ties
        # 21 lengths behind mid-field is held at the floor, not sent to -700.
        # Stored to one decimal it can read 90.0; unrounded it never gets there
        # (TestFieldFigure.test_bends_toward_the_limit_and_never_reaches_it).
        assert figs[-1] >= 100 - et.FIELD_LIMIT
        assert 103 < figs[0] < 105                    # a winner ~4L clear of mid-field

    def test_track_speed_does_not_move_it(self, synthetic_runs, one_field):
        """The point of the change: the same race run a second slower -- a
        heavy track, a crawl of a pace -- leaves every runner's figure where it
        was and moves only the time reading."""
        slow = one_field.copy()
        slow["finish_time"] = slow["finish_time"] + 1.0
        a = self._out(synthetic_runs, one_field)
        b = self._out(synthetic_runs, slow)
        assert b["figure"].to_numpy() == pytest.approx(a["figure"].to_numpy())
        assert (b["time_figure"].to_numpy() < a["time_figure"].to_numpy()).all()
