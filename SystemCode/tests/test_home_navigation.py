"""Homepage navigation regression tests using isolated project data."""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "app"
sys.path.insert(0, str(APP_DIR))


class HomeNavigationTest(unittest.TestCase):
    def setUp(self):
        # Import after collection so other service tests can set their own data directory.
        from tcui import config, demo, service, store

        self.config, self.demo, self.service, self.store = config, demo, service, store
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tendercheck_home_")
        self.addCleanup(self.temp_dir.cleanup)
        data_dir = Path(self.temp_dir.name)
        for name, value in (("DATA_DIR", data_dir), ("DB_PATH", data_dir / "tendercheck.db"),
                            ("PROJECTS_DIR", data_dir / "projects")):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        store.init_db()

    def app(self):
        app = AppTest.from_file(str(APP_DIR / "main.py"), default_timeout=20).run()
        self.assertFalse(app.exception)
        return app

    def button(self, app, label):
        return next(button for button in app.button if button.label == label)

    def assert_stage(self, app, label):
        self.assertFalse(app.exception)
        self.assertIn(f"Next step · {label}", [item.value for item in app.subheader])

    def next_step(self, pid):
        from tcui import ui

        return ui.project_next_step(self.store.get_project(pid), self.service.review_readiness(pid),
                                    self.store.latest_round(pid))

    def completed_steps(self, pid):
        from tcui import ui

        return ui.workflow_completed_steps(self.store.get_project(pid), self.service.review_readiness(pid),
                                            self.store.latest_round(pid))

    def prepare_demo(self):
        pid = self.demo.create_demo_project()
        for requirement in self.store.pending_amendments(pid):
            self.service.decide_amendment(requirement["id"], True, "Ann", "Reviewer")
        self.service.confirm_requirements(pid, "Ann", "Reviewer")
        return pid

    def test_landing_and_demo_entry(self):
        app = self.app()
        self.assertIn("New project", [item.value for item in app.subheader])
        self.assertIn("No file upload needed.", " ".join(item.value for item in app.markdown))
        workflow = " ".join(item.value for item in app.markdown)
        for number, step in enumerate(("Documents", "Requirements", "Review", "Remediation", "Sign-off"), 1):
            self.assertIn(f"{number} · {step}", workflow)
        details = next(item for item in app.expander if item.label == "What's included")
        self.assertFalse(details.proto.expanded)
        self.assertFalse(app.get("form")[0].proto.form.border)
        self.button(app, "Explore demo").click().run()
        self.assert_stage(app, "Requirements")
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertEqual(len(self.store.list_documents(app.session_state["project_id"])), 3)

    def test_create_second_project_and_reopen_original(self):
        original = self.store.create_project("Original project")
        marker = self.store.project_dir(original) / "keep.txt"
        marker.write_text("Existing project files", encoding="utf-8")
        app = self.app()
        self.assert_stage(app, "Documents")
        # The shared button must also work from a business page.
        app.switch_page("views/documents.py").run()
        app.button(key="nav_new_project").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "TenderCheck")
        self.assertEqual(app.session_state["project_id"], original)
        # AppTest retains a manually requested page across run() calls, even after st.switch_page().
        app.switch_page("views/overview.py").run()
        self.button(app, "Create project").click().run()
        self.assertIn("Enter a project name.", [item.value for item in app.error])
        self.assertEqual(len(self.store.list_projects()), 1)
        next(item for item in app.text_input if item.label == "Project name").set_value("Second project")
        self.button(app, "Create project").click().run()
        self.assertFalse(app.exception)
        second = app.session_state["project_id"]
        self.assertNotEqual(original, second)
        self.assertEqual(len(self.store.list_projects()), 2)
        self.assertEqual(app.title[0].value, "Documents")
        self.assertEqual(marker.read_text(encoding="utf-8"), "Existing project files")
        self.assertEqual(self.store.list_documents(original), [])
        app.switch_page("views/overview.py").run()
        self.assert_stage(app, "Documents")
        project_picker = next(item for item in app.selectbox if item.label == "Project")
        project_picker.select(original).run()
        self.assertEqual(app.session_state["project_id"], original)
        self.assertEqual(app.title[0].value, "Original project")
        app.button(key="nav_new_project").click().run()
        self.button(app, "Back to current project").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Original project")
        app.button(key="nav_new_project").click().run()
        next(item for item in app.selectbox if item.label == "Project").select(original).run()
        self.assertEqual(app.title[0].value, "Original project")

    def test_next_step_follows_review_remediation_and_signoff(self):
        pid = self.prepare_demo()
        self.assertEqual(self.next_step(pid)[::2], ("Review", "overview"))
        self.assertEqual(self.completed_steps(pid), ["Documents", "Requirements"])
        app = self.app()
        self.assert_stage(app, "Review")
        review_job = self.store.create_job(pid, "review")
        self.assertIn("running", self.next_step(pid)[1])
        self.store.update("jobs", review_job, {"status": "done"})
        round_id = self.service._review_job(self.service._no_progress, pid)["round_id"]
        self.assertEqual(self.next_step(pid)[::2], ("Review", "workspace"))
        self.assertNotIn("Review", self.completed_steps(pid))
        app.run()
        self.assert_stage(app, "Review")
        for result in self.store.list_results(round_id):
            if result["verdict"] == self.config.COMPLIANT:
                self.service.confirm_result(result["id"], "Ann", "Reviewer")
            else:
                self.service.send_to_remediation(result["id"], "", "Ann", "Reviewer")
        self.assertEqual(self.next_step(pid)[::2], ("Remediation", "remediation"))
        self.assertIn("Review", self.completed_steps(pid))
        self.assertNotIn("Remediation", self.completed_steps(pid))
        app.run()
        self.assert_stage(app, "Remediation")
        # Existing sign-off permits disclosed open risks; guidance must keep that path accessible.
        self.assertEqual(self.service.signoff_blockers(round_id), [])
        self.service.sign_round(round_id, "Ben", "Open issues disclosed", "Manager")
        self.assertEqual(self.next_step(pid)[::2], ("Sign-off", "report"))
        self.assertIn("Sign-off", self.completed_steps(pid))
        self.assertNotIn("Remediation", self.completed_steps(pid))
        app.run()
        self.assert_stage(app, "Sign-off")
        # A later bid version must take precedence over the older signed-off round.
        path = self.demo.revised_bid_path(pid)
        bid_id, _ = self.service.add_document(pid, "bid", path.name, path.read_bytes(), background=False)
        self.service.link_revised_bid(pid, bid_id, "Ann", "Reviewer")
        self.assertEqual(self.next_step(pid)[::2], ("Remediation", "remediation"))
        self.assertNotIn("Review", self.completed_steps(pid))
        self.assertNotIn("Sign-off", self.completed_steps(pid))
        second_round = self.service._review_job(self.service._no_progress, pid)["round_id"]
        for result in self.store.list_results(second_round):
            if result["human_status"] == self.config.UNREVIEWED:
                self.service.confirm_result(result["id"], "Ann", "Reviewer")
        self.assertEqual(self.next_step(pid)[::2], ("Review", "workspace"))
        self.assertNotIn("Review", self.completed_steps(pid))

    def test_documents_take_priority_over_unconfirmed_requirements(self):
        pid = self.store.create_project("Empty project")
        self.assertEqual(self.next_step(pid)[::2], ("Documents", "documents"))
        demo_id = self.demo.create_demo_project()
        self.assertEqual(self.next_step(demo_id)[::2], ("Requirements", "requirements"))
        self.store.update("projects", demo_id, {"req_confirmed_at": self.store.now()})
        self.assertEqual(self.next_step(demo_id)[::2], ("Requirements", "requirements"))

    def test_start_review_button_and_ready_signoff(self):
        pid = self.prepare_demo()
        app = self.app()
        self.button(app, "Start review").click().run()
        self.assertFalse(app.exception)
        deadline = time.monotonic() + 10
        while self.store.active_jobs(pid, "review") and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.store.active_jobs(pid, "review"))
        rnd = self.store.latest_round(pid)
        self.assertIsNotNone(rnd)
        for result in self.store.list_results(rnd["id"]):
            self.service.confirm_result(result["id"], "Ann", "Reviewer")
        app.run()
        self.assert_stage(app, "Sign-off")
        self.assertEqual(self.next_step(pid)[::2], ("Sign-off", "report"))
        path = self.demo.revised_bid_path(pid)
        self.service.add_document(pid, "bid", path.name, path.read_bytes(), background=False)
        self.assertEqual(self.next_step(pid)[::2], ("Review", "overview"))

    def test_sidebar_navigation_keeps_identity_and_can_leave_new_project(self):
        from tcui import ui

        pid = self.store.create_project("A long project name for the review workspace and navigation")
        app = self.app()
        next(item for item in app.text_input if item.label == "Your name").set_value("Pat Reviewer").run()
        expected_pages = [label for pages in ui.NAVIGATION.values() for _, label, _ in pages]
        self.assertEqual([item.value for item in app.sidebar.page_link], expected_pages)
        sections = [item.value for item in app.sidebar.caption]
        self.assertLess(sections.index("Projects"), sections.index("Workspace"))
        self.assertLess(sections.index("Delivery"), sections.index("Reviewer details"))
        self.assertLess(sections.index("Reviewer details"), sections.index("Data processing"))
        for page in ui.PAGES.values():
            app.switch_page(page).run()
            self.assertFalse(app.exception, page)
            self.assertEqual(app.session_state["user_name"], "Pat Reviewer")
            self.assertEqual(app.session_state["project_id"], pid)
            self.assertIsNotNone(app.button(key="nav_new_project"))
        app.button(key="nav_new_project").click().run()
        self.assertEqual(app.title[0].value, "TenderCheck")
        app.switch_page("views/documents.py").run()
        app.switch_page("views/overview.py").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, self.store.get_project(pid)["name"])

    def test_readiness_history_and_external_processing_notice(self):
        pid = self.store.create_project("Empty project")
        app = self.app()
        readiness = next(item for item in app.expander if item.label.startswith("Readiness"))
        self.assertTrue(readiness.proto.expanded)
        self.assertEqual(self.completed_steps(pid), [])
        demo_id = self.prepare_demo()
        app = self.app()
        readiness = next(item for item in app.expander if item.label.startswith("Readiness"))
        self.assertFalse(readiness.proto.expanded)
        self.assertTrue(any(":blue-badge[" in item.value and "Next" in item.value for item in app.markdown))
        self.service._review_job(self.service._no_progress, demo_id)
        app.run()
        history = next(item for item in app.expander if item.label == "All review rounds (1)")
        self.assertFalse(history.proto.expanded)
        with patch.object(self.config, "LLM_PROVIDER", "Example provider"):
            app.run()
            self.assertTrue(any("Example provider" in item.value for item in app.sidebar.warning))
            self.assertFalse(next(item for item in app.expander if item.label == "Engine details").proto.expanded)


if __name__ == "__main__":
    unittest.main()
