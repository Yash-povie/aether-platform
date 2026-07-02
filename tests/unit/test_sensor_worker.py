import pytest
import io
import pandas as pd
from unittest.mock import patch, MagicMock


def test_analyze_timeseries_basic():
    """Timeseries analysis returns correct row count, stats, and detects outliers."""
    from services.ingest_workers.sensor_worker import analyze_timeseries

    data = pd.DataFrame(
        {
            "temp": [20.0, 21.0, 22.0, 19.5, 80.0],   # 80.0 is an outlier
            "pressure": [1013, 1012, 1014, 1013, 1011],
        }
    )
    result = analyze_timeseries(data)

    assert result["num_rows"] == 5
    assert "temp" in result["statistics"]
    assert "pressure" in result["statistics"]
    # 80.0 should be flagged as anomaly (z-score / IQR outlier)
    assert len(result["anomalies"]) >= 1


def test_analyze_timeseries_empty():
    """Empty DataFrame produces zero rows and no anomalies."""
    from services.ingest_workers.sensor_worker import analyze_timeseries

    data = pd.DataFrame({"x": []})
    result = analyze_timeseries(data)

    assert result["num_rows"] == 0
    assert result.get("anomalies", []) == []


def test_analyze_timeseries_statistics_keys():
    """Statistics dict must contain mean, std, min, max per column."""
    from services.ingest_workers.sensor_worker import analyze_timeseries

    data = pd.DataFrame({"val": [1.0, 2.0, 3.0, 4.0, 5.0]})
    result = analyze_timeseries(data)

    stats = result["statistics"]["val"]
    assert "mean" in stats
    assert "std" in stats
    assert "min" in stats
    assert "max" in stats


def test_analyze_timeseries_no_false_anomalies():
    """Uniform data should produce zero anomalies."""
    from services.ingest_workers.sensor_worker import analyze_timeseries

    data = pd.DataFrame({"val": [10.0] * 20})
    result = analyze_timeseries(data)

    assert len(result.get("anomalies", [])) == 0


def test_analyze_timeseries_multiple_outliers():
    """Multiple outliers in multiple columns are all detected."""
    from services.ingest_workers.sensor_worker import analyze_timeseries

    data = pd.DataFrame(
        {
            "a": [1.0, 1.0, 1.0, 1.0, 100.0],
            "b": [50.0, 50.0, 50.0, 50.0, 0.0],
        }
    )
    result = analyze_timeseries(data)
    assert len(result["anomalies"]) >= 2