from pathlib import Path
import unittest


WEB_ROOT = Path(__file__).parents[1] / "src" / "project_compass" / "web"


class WebAssetsTest(unittest.TestCase):
    def test_page_exposes_lens_controls_and_evidence_drawer(self):
        html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-lens="personal"', html)
        self.assertIn('data-lens="team"', html)
        self.assertIn('id="evidence-drawer"', html)
        self.assertIn('id="evidence-drawer" class="drawer"', html)
        self.assertIn('hidden', html)
        self.assertIn('src="/app.js"', html)

    def test_script_renders_candidate_and_confirmed_relation_labels(self):
        script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
        html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn("candidate", script)
        self.assertIn("confirmed", script)
        self.assertIn("/api/evidence?id=", script)
        self.assertIn("/api/review", script)
        self.assertIn("Confirm support", script)
        self.assertIn("Confirm as team fact", script)
        self.assertIn("review_history", script)
        self.assertIn("/api/investigate", script)
        self.assertIn("formatTime", script)
        self.assertIn("observed_at", script)
        self.assertIn("evidence-filter", html)
        self.assertIn("筛选证据", html)
        self.assertIn("Ask Compass", html)

    def test_layout_allows_long_evidence_titles_without_page_overflow(self):
        css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: 10px minmax(0, 1fr) auto", css)
        self.assertIn("min-width: 0", css)
        self.assertIn("overflow-x: hidden", css)
        self.assertIn(".evidence-item > span", css)


if __name__ == "__main__":
    unittest.main()
