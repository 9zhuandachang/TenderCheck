"""Homepage navigation regression tests for the 3-step Overview workflow (real Chinese backend trunk)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
import streamlit as st
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "app"
sys.path.insert(0, str(APP_DIR))


def _make_cn_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 80), text, fontsize=11, fontname="china-ss")
    data = doc.tobytes()
    doc.close()
    return data


class HomeNavigationTest(unittest.TestCase):
    def setUp(self):
        from tcui import config, service, store

        self.config, self.service, self.store = config, service, store
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tendercheck_home_")
        self.addCleanup(self.temp_dir.cleanup)
        data_dir = Path(self.temp_dir.name)
        for name, value in (
            ("DATA_DIR", data_dir),
            ("DB_PATH", data_dir / "tendercheck.db"),
            ("PROJECTS_DIR", data_dir / "projects"),
        ):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env_patcher = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        store.init_db()

    def app(self):
        app = AppTest.from_file(str(APP_DIR / "main.py"), default_timeout=20).run()
        self.assertFalse(app.exception)
        return app

    def button(self, app, label):
        return next(button for button in app.button if button.label == label)

    def prepare_chinese_project(self):
        pid = self.store.create_project("滁河防洪治理工程（导航测试）")
        tender_pdf = _make_cn_pdf_bytes(
            "第二章 投标人须知前附表\n1.3.2 计划工期不超过360日历天。\n3.4.1 投标保证金不少于50万元。"
        )
        bid_pdf = _make_cn_pdf_bytes(
            "投标函\n我方承诺本工程计划工期为350日历天。\n投标保证金金额为60万元。"
        )
        tdoc_id, _ = self.service.add_document(pid, "tender", "招标文件.pdf", tender_pdf, background=False)
        self.service.add_document(pid, "bid", "投标文件.pdf", bid_pdf, background=False)
        self.service.add_requirement(
            pid, "T0001", "计划工期不超过360日历天。", "Schedule", tdoc_id, 1, "导入测试条款", "Ann", "Reviewer"
        )
        self.service.add_requirement(
            pid, "T0002", "投标保证金不少于50万元。", "Commercial", tdoc_id, 1, "导入测试条款", "Ann", "Reviewer"
        )
        self.service.confirm_requirements(pid, "Ann", "Reviewer")
        return pid

    def test_landing_and_create_project(self):
        app = self.app()
        self.assertEqual(app.title[0].value, "TenderCheck")
        subheaders = [item.value for item in app.subheader]
        self.assertIn("New project", subheaders)
        next(item for item in app.text_input if item.label == "Project name").set_value("水利工程审标工作区")
        self.button(app, "Create project").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self.store.list_projects()), 1)
        subheaders_after = [item.value for item in app.subheader]
        self.assertIn("Step 1 · Confirm Project Documents", subheaders_after)
        self.assertIn("Step 2 · Extract Information, Build RAG & Run Review", subheaders_after)

    def test_create_second_project_and_reopen_original(self):
        original = self.store.create_project("Original project")
        marker = self.store.project_dir(original) / "keep.txt"
        marker.write_text("Existing project files", encoding="utf-8")
        app = self.app()
        self.assertIn("Step 1 · Confirm Project Documents", [item.value for item in app.subheader])
        app.button(key="nav_new_project").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "TenderCheck")
        self.assertEqual(app.session_state["project_id"], original)
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
        self.assertEqual(app.title[0].value, "Second project")
        self.assertEqual(marker.read_text(encoding="utf-8"), "Existing project files")
        project_picker = next(item for item in app.selectbox if item.label == "Project")
        project_picker.select(original).run()
        self.assertEqual(app.session_state["project_id"], original)
        self.assertEqual(app.title[0].value, "Original project")

    def test_overview_three_step_pipeline_and_ready_state(self):
        pid = self.prepare_chinese_project()
        app = self.app()
        subheaders = [item.value for item in app.subheader]
        self.assertIn("Step 1 · Confirm Project Documents", subheaders)
        self.assertIn("Step 2 · Extract Information, Build RAG & Run Review", subheaders)
        self.assertNotIn("Step 3 · Review Ready — Enter Workspace", subheaders)
        self.button(app, "Confirm documents & start review pipeline").click().run()
        self.assertFalse(app.exception)
        rnd = self.store.latest_round(pid)
        self.assertIsNotNone(rnd)
        self.assertEqual(rnd["status"], "done")
        subheaders_done = [item.value for item in app.subheader]
        self.assertIn("Step 3 · Review Ready — Enter Workspace", subheaders_done)
        self.assertIsNotNone(self.button(app, "Open Review Workspace"))

    def test_sidebar_navigation_clean_without_role_or_documents(self):
        from tcui import ui

        pid = self.store.create_project("A long project name for the review workspace and navigation")
        app = self.app()
        expected_pages = [label for pages in ui.NAVIGATION.values() for _, label, _ in pages]
        page_links = [
            getattr(item, "label", getattr(getattr(item, "proto", None), "label", ""))
            for item in app.get("page_link")
        ]
        self.assertEqual(page_links, expected_pages)
        self.assertNotIn("Documents", expected_pages)
        self.assertFalse(any(item.label == "Your name" for item in app.text_input))
        for page in ui.PAGES.values():
            app.switch_page(page).run()
            self.assertFalse(app.exception, page)
            self.assertEqual(app.session_state["project_id"], pid)
            self.assertIsNotNone(app.button(key="nav_new_project"))


if __name__ == "__main__":
    unittest.main()
