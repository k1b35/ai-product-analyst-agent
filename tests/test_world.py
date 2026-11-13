"""Свойства синтетического мира: инциденты действительно такие, как заявлено в scenarios.json."""
import sqlite3


def q(world, sql):
    con = sqlite3.connect(world)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_mix_shift_not_within_channel_drop(world):
    rows = q(world, """
        SELECT u.channel, a.date >= date((SELECT MAX(date) FROM daily_activity), '-27 day') AS recent,
               AVG(a.trades > 0) FROM daily_activity a JOIN users u USING(user_id) GROUP BY 1, 2""")
    conv = {(c, r): v for c, r, v in rows}
    for ch in ("organic", "referral", "ads_vk", "ads_yandex"):
        assert abs(conv[(ch, 1)] / conv[(ch, 0)] - 1) < 0.1          # внутри канала конверсия стабильна
    assert conv[("blogger", 1)] < 0.4 * conv[("organic", 1)]          # блогерский трафик конвертируется хуже


def test_blogger_share_grows(world):
    (old,), = q(world, "SELECT AVG(u.channel='blogger') FROM daily_activity a JOIN users u USING(user_id) "
                       "WHERE a.date < date((SELECT MAX(date) FROM daily_activity), '-40 day')")
    (new,), = q(world, "SELECT AVG(u.channel='blogger') FROM daily_activity a JOIN users u USING(user_id) "
                       "WHERE a.date >= date((SELECT MAX(date) FROM daily_activity), '-7 day')")
    assert old < 0.02 and new > 0.12


def test_tables_exist(world):
    names = {r[0] for r in q(world, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "daily_activity", "posts", "experiments", "ab_assignments", "ab_outcomes",
            "daily_metrics", "daily_metrics_by_platform"} <= names
