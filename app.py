# ============================================================
# ❤️ سیستم احساسات نسبت به هر کاربر (مکانیزم پایتون)
# ============================================================
class AttitudeTracker:
    ...
    def __init__(self, path: str):
        self.path = path  # مسیر فایل (همان roxie_attitude.json)
        self.scores: Dict[int, int] = {}
        self._load()

    def _load(self):
        # 🟢 خوندن فایل JSON؛ اگر فایل وجود نداشته باشه ارور نمیده و لیست رو خالی میذاره
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.scores = {int(k): int(v) for k, v in data.items()}
        except Exception:
            self.scores = {}

    def _save(self):
        # 🟢 ساختن یا بروزرسانی خودکار فایل JSON در سیستم
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.scores, f)
        except Exception as e:
            logger.warning(f"Could not save attitude file: {e}")
