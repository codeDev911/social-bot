# lib/check_features.py
class CheckFeatures:
    def __init__(self, user: dict, features: any, url: str):
        self.user = user
        self.features = features
        self.url = url

    def can_access(self):
        # 1. Check if endpoint allowed
        if self.url not in self.features.url:
            return False, f"Your plan does not allow access to {self.url}"

        # 2. Check limits
        for limit_name, max_allowed in (self.features.limits or {}).items():
            used = self.user.get(limit_name, 0)   # user must track usage by same key
            if used >= max_allowed:
                return False, f"Limit exceeded: {limit_name} ({used}/{max_allowed})"

        # 3. Check flags (boolean features)
        for flag_name, allowed in (self.features.flags or {}).items():
            if not allowed and flag_name in self.url:  # simple matching by name
                return False, f"{flag_name.replace('_',' ').title()} not included in your plan"

        return True, "Allowed"
