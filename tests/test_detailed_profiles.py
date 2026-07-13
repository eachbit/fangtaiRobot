import json
import math
import tempfile
import unittest
from pathlib import Path

from app.data_loader import load_users


HEIGHT = "身高_cm"
WEIGHT = "体重_kg"
BMI = "BMI"
METRICS = "体检指标"
GLUCOSE = "空腹血糖_mmol/L"
BLOOD_PRESSURE = "血压_mmHg"
TOTAL_CHOLESTEROL = "总胆固醇_mmol/L"
TRIGLYCERIDES = "甘油三酯_mmol/L"
LDL = "低密度脂蛋白_mmol/L"
HDL = "高密度脂蛋白_mmol/L"
URIC_ACID = "尿酸_umol/L"
GENDER = "性别"
AGE = "年龄"
LABOR_INTENSITY = "劳动强度"
SPECIAL_GROUPS = "特殊人群"
PREGNANCY_WEEK = "孕周期"
TASTE_PREFERENCE = "口味偏好"
ALLERGENS = "过敏食材"
HEALTH_GOALS = "健康需求"
METRIC_KEYS = {
    GLUCOSE,
    BLOOD_PRESSURE,
    TOTAL_CHOLESTEROL,
    TRIGLYCERIDES,
    LDL,
    HDL,
    URIC_ACID,
}


def detailed_user(user_id: int = 99) -> dict:
    return {
        "id": user_id,
        "性别": "男",
        "年龄": 30,
        "劳动强度": "中",
        "特殊人群": [],
        "孕周期": None,
        "口味偏好": "清淡",
        "过敏食材": [],
        "健康需求": [],
        HEIGHT: 180.1,
        WEIGHT: 78.2,
        BMI: 24.1,
        METRICS: {
            GLUCOSE: 7.3,
            BLOOD_PRESSURE: "146/93",
            TOTAL_CHOLESTEROL: 4.63,
            TRIGLYCERIDES: 1.16,
            LDL: 2.58,
            HDL: 1.29,
            URIC_ACID: 271,
        },
    }


class DetailedProfileTests(unittest.TestCase):
    def load_temporary_users(self, payload: object):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=True), encoding="utf-8")
            return load_users(path)

    def assert_invalid(self, item: dict, field: str) -> None:
        with self.assertRaisesRegex(ValueError, rf"99.*{field}"):
            self.load_temporary_users([item])

    def test_loads_all_detailed_profiles_and_preserves_raw_fields(self):
        users = load_users()

        self.assertEqual(50, len(users))
        self.assertEqual(50, len({user.id for user in users}))
        self.assertTrue(all(user.height_cm > 0 and user.weight_kg > 0 and user.bmi > 0 for user in users))
        self.assertEqual(40, sum(user.checkup_metrics is not None for user in users))
        self.assertEqual(10, sum(user.checkup_metrics is None for user in users))

        user = next(user for user in users if user.id == 3)
        self.assertEqual((180.1, 78.2, 24.1), (user.height_cm, user.weight_kg, user.bmi))
        self.assertEqual(7.3, user.checkup_metrics.fasting_glucose_mmol_l)
        self.assertEqual(146, user.checkup_metrics.systolic_blood_pressure_mm_hg)
        self.assertEqual(93, user.checkup_metrics.diastolic_blood_pressure_mm_hg)
        self.assertEqual(4.63, user.checkup_metrics.total_cholesterol_mmol_l)
        self.assertEqual(1.16, user.checkup_metrics.triglycerides_mmol_l)
        self.assertEqual(2.58, user.checkup_metrics.ldl_mmol_l)
        self.assertEqual(1.29, user.checkup_metrics.hdl_mmol_l)
        self.assertEqual(271, user.checkup_metrics.uric_acid_umol_l)
        self.assertEqual(180.1, user.raw[HEIGHT])
        self.assertEqual("146/93", user.raw[METRICS][BLOOD_PRESSURE])

    def test_all_profile_bmis_match_measurements(self):
        for user in load_users():
            self.assertLessEqual(abs(user.weight_kg / (user.height_cm / 100) ** 2 - user.bmi), 0.1)

    def test_rejects_invalid_identifiers_and_missing_measurements(self):
        for invalid_id in (True, 0, -1, 1.5, "1"):
            item = detailed_user(invalid_id)
            with self.assertRaisesRegex(ValueError, r".*id"):
                self.load_temporary_users([item])

        duplicate = detailed_user()
        with self.assertRaisesRegex(ValueError, r"99.*id"):
            self.load_temporary_users([detailed_user(), duplicate])

        for field in (HEIGHT, WEIGHT, BMI):
            item = detailed_user()
            item.pop(field)
            self.assert_invalid(item, field)

    def test_rejects_non_list_roots_and_non_object_entries(self):
        for payload in ({}, None, "not a list"):
            with self.assertRaisesRegex(ValueError, r"top-level"):
                self.load_temporary_users(payload)

        for payload in ([None], ["not a user"], [42]):
            with self.assertRaisesRegex(ValueError, r"index 0"):
                self.load_temporary_users(payload)

    def test_rejects_missing_unknown_or_invalid_base_fields(self):
        item = detailed_user()
        item.pop(GENDER)
        self.assert_invalid(item, GENDER)

        item = detailed_user()
        item["typo"] = "value"
        self.assert_invalid(item, "typo")

        invalid_values = (
            (GENDER, 1),
            (AGE, True),
            (AGE, 0),
            (LABOR_INTENSITY, []),
            (SPECIAL_GROUPS, "not-a-list"),
            (SPECIAL_GROUPS, [1]),
            (PREGNANCY_WEEK, 12),
            (TASTE_PREFERENCE, None),
            (ALLERGENS, "milk"),
            (HEALTH_GOALS, [False]),
        )
        for field, value in invalid_values:
            item = detailed_user()
            item[field] = value
            self.assert_invalid(item, field)

    def test_rejects_invalid_measurement_values_and_bmi(self):
        for field, value in ((HEIGHT, True), (WEIGHT, math.nan), (BMI, math.inf), (HEIGHT, 0)):
            item = detailed_user()
            item[field] = value
            self.assert_invalid(item, field)

        item = detailed_user()
        item[BMI] = 25.0
        self.assert_invalid(item, BMI)

    def test_rejects_imprecise_or_overflowing_integer_measurements(self):
        for field, value in ((HEIGHT, 2**53 + 1), (WEIGHT, 10**400)):
            item = detailed_user()
            item[field] = value
            with self.assertRaisesRegex(ValueError, rf"99.*{field}.*exact|99.*{field}.*finite"):
                self.load_temporary_users([item])

    def test_rejects_measurements_outside_technical_sanity_ranges(self):
        for field, value in ((HEIGHT, 49.9), (HEIGHT, 250.1), (WEIGHT, 9.9), (WEIGHT, 500.1), (BMI, 4.9), (BMI, 100.1)):
            item = detailed_user()
            item[field] = value
            self.assert_invalid(item, field)

    def test_bmi_validation_uses_unrounded_calculation(self):
        item = detailed_user()
        item[HEIGHT] = 180
        item[WEIGHT] = 64.8
        item[BMI] = 20.1
        self.assertEqual(20.1, self.load_temporary_users([item])[0].bmi)

        item = detailed_user()
        item[HEIGHT] = 180
        item[WEIGHT] = 78.0516
        item[BMI] = 24.2
        with self.assertRaisesRegex(ValueError, rf"99.*{BMI}"):
            self.load_temporary_users([item])

        item[BMI] = 24.189
        self.assertEqual(24.189, self.load_temporary_users([item])[0].bmi)

    def test_rejects_invalid_checkup_shape_and_values(self):
        for blood_pressure in ("146-93", "93/146", "0/93"):
            item = detailed_user()
            item[METRICS][BLOOD_PRESSURE] = blood_pressure
            self.assert_invalid(item, BLOOD_PRESSURE)

        item = detailed_user()
        item[METRICS].pop(HDL)
        self.assert_invalid(item, METRICS)

        item = detailed_user()
        item[METRICS]["未知指标"] = 1
        self.assert_invalid(item, METRICS)

        for field, value in ((GLUCOSE, math.nan), (URIC_ACID, True), (URIC_ACID, 271.5), (LDL, 0)):
            item = detailed_user()
            item[METRICS][field] = value
            self.assert_invalid(item, field)

    def test_rejects_checkup_values_outside_technical_sanity_ranges(self):
        for blood_pressure in ("999/10", "301/100", "120/19"):
            item = detailed_user()
            item[METRICS][BLOOD_PRESSURE] = blood_pressure
            self.assert_invalid(item, BLOOD_PRESSURE)

        for field, value in ((GLUCOSE, 0.09), (GLUCOSE, 100.1), (LDL, 100.1), (URIC_ACID, 9), (URIC_ACID, 2001)):
            item = detailed_user()
            item[METRICS][field] = value
            self.assert_invalid(item, field)


if __name__ == "__main__":
    unittest.main()
