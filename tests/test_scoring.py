import re
import unittest
from datetime import date

from test_ui import UiTestCase


class ScoringPageTests(UiTestCase):
    def seed(self):
        from core.models import Personnel, Project, Task
        with self.app.app_context():
            p1 = Project(name='P1', status='進行中', category='c', rep='r', start_date=date(2025, 1, 1))
            p2 = Project(name='P2', status='進行中', category='c', rep='r', start_date=date(2025, 1, 1))
            self.db.session.add_all([p1, p2, Personnel(name='Alice', display_name='小愛')])
            self.db.session.flush()
            self.db.session.add_all([
                Task(personnel='Alice', project_id=p1.id, date=date(2025, 3, 1), day_hours=8, overtime_hours=2, description='a'),
                Task(personnel='Alice', project_id=p1.id, date=date(2025, 3, 2), night_hours=1.5, description='b'),
                Task(personnel='Bob', project_id=p1.id, date=date(2025, 12, 31), day_hours=4, description='c'),
                Task(personnel='Alice', project_id=p2.id, date=date(2025, 1, 1), day_hours=3, description='d'),
                Task(personnel='Alice', project_id=p2.id, date=date(2024, 12, 31), day_hours=100, description='other year'),
            ])
            self.db.session.commit()
            return p1.id, p2.id

    def get(self, url):
        resp = self.app.test_client().get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_one_row_per_project_with_scores_and_links(self):
        p1, p2 = self.seed()
        html = self.get('/scoring?year=2025')
        table = re.search(r'<table id="scoring-table".*?</table>', html, re.S).group(0)
        for header in ('專案', '員工評分'):
            self.assertRegex(table, rf'<th[^>]*>{header}</th>')
        self.assertEqual(len(re.findall(r'<tbody.*?</tbody>', table, re.S)), 1)
        rows = re.findall(r'<tr id="project-\d+" class="align-top.*?</tr>', table, re.S)
        self.assertEqual(len(rows), 2)               # one row per project
        self.assertNotIn('rowspan', table)
        self.assertNotIn('日班', table)               # per-shift hours are gone
        self.assertIn(f'href="/project/{p1}"', rows[0])
        self.assertIn('總工時 15.5 小時', rows[0])     # P1: 8 + 2 + 1.5 + 4
        self.assertIn(f'href="/employee-case?person=Alice&amp;project_id={p1}"', rows[0])
        self.assertIn(f'href="/employee-case?person=Bob&amp;project_id={p1}"', rows[0])
        self.assertEqual(rows[0].count('小愛'), 1)    # merged column: name is not repeated
        self.assertEqual(len(re.findall(r'<th[ >]', table)), 2)
        chips = re.findall(r'score-chip.*?<span[^>]*>([^<]+)</span>\s*<span[^>]*>([\d.]+)</span>', rows[0], re.S)
        self.assertEqual(chips, [('小愛', '11.5'), ('Bob', '4')])   # sorted by score; display name
        self.assertIn(f'href="/project/{p2}"', rows[1])
        self.assertNotIn('100', table)               # 2024 hours stay out of the 2025 scope

    def test_overview_totals_cross_projects(self):
        self.seed()
        html = self.get('/scoring?year=2025')
        overview = html[:html.index('id="scoring-table"')]
        self.assertRegex(overview, r'小愛</div>\s*<div[^>]*><span[^>]*>14\.5</span>')   # 11.5 + 3
        self.assertRegex(overview, r'Bob</div>\s*<div[^>]*><span[^>]*>4</span>')

    def test_invalid_year_falls_back_to_current_year(self):
        self.seed()
        for bad in ('abc', '0', '9999', '99999999999999999999'):
            self.assertIn(f'value="{date.today().year}"', self.get(f'/scoring?year={bad}'))

    def test_year_selector_has_this_year_and_input_only(self):
        self.seed()
        this_year = date.today().year
        html = self.get(f'/scoring?year={this_year}')
        self.assertNotIn('去年', html)
        self.assertRegex(html, r'aria-current="true"\s+class="[^"]*bg-primary[^"]*">今年</a>')
        self.assertNotIn('其他', html)
        self.assertIn('aria-label="評分年度"', html)
        other = self.get('/scoring?year=2025')
        self.assertNotIn('去年', other)
        self.assertRegex(other, r'class="[^"]*text-ink-muted[^"]*">今年</a>')   # not highlighted
        self.assertNotRegex(other, r'bg-primary[^"]*">今年</a>')
        self.assertIn('value="2025"', other)

    def post_weight(self, client, pid, weight, year=2025):
        return client.post(f'/scoring/weight/{pid}', data={'weight': weight, 'year': year})

    def weight_of(self, pid):
        from core.models import Project
        with self.app.app_context():
            return self.db.session.get(Project, pid).weight

    def test_weight_scales_scores_not_project_hours(self):
        p1, p2 = self.seed()
        self.assertEqual(self.weight_of(p1), 1)
        resp = self.post_weight(self.admin_client(), p1, '1.5')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('year=2025', resp.headers['Location'])
        self.assertIn(f'#project-{p1}', resp.headers['Location'])
        html = self.get('/scoring?year=2025')    # anonymous viewer: weighted scores, no input
        table = re.search(r'<table id="scoring-table".*?</table>', html, re.S).group(0)
        rows = re.findall(r'<tr id="project-\d+" class="align-top.*?</tr>', table, re.S)
        self.assertIn('總工時 15.5 小時', rows[0])    # raw hours, unweighted
        chips = re.findall(r'score-chip.*?<span[^>]*>([^<]+)</span>\s*<span[^>]*>([\d.]+)</span>', rows[0], re.S)
        self.assertEqual(chips, [('小愛', '17.25'), ('Bob', '6')])
        overview = html[:html.index('id="scoring-table"')]
        self.assertRegex(overview, r'小愛</div>\s*<div[^>]*><span[^>]*>20\.25</span>')   # 17.25 + 3
        self.assertNotIn('name="weight"', html)
        self.assertIn('name="weight"', self.admin_client().get('/scoring?year=2025').get_data(as_text=True))

    def test_invalid_weight_rejected(self):
        p1, _ = self.seed()
        admin = self.admin_client()
        for bad in ('0', '0.0', '-0', '-1', 'abc', '', 'nan', 'inf', '1000.5', '1e308'):
            self.post_weight(admin, p1, bad)
            self.assertEqual(self.weight_of(p1), 1, bad)

    def test_weight_requires_admin(self):
        p1, _ = self.seed()
        self.post_weight(self.app.test_client(), p1, '5')
        self.assertEqual(self.weight_of(p1), 1)
        self.assertEqual(self.app.test_client().get(f'/scoring/weight/{p1}').status_code, 405)

    def test_ensure_task_columns_adds_weight_to_old_db(self):
        from sqlalchemy import text
        from core.helpers import ensure_task_columns
        with self.app.app_context():
            with self.db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE project DROP COLUMN weight'))
                conn.commit()
            ensure_task_columns()
            with self.db.engine.connect() as conn:
                self.assertIn('weight', {r[1] for r in conn.execute(text('PRAGMA table_info(project)'))})

    def test_empty_year(self):
        self.seed()
        self.assertIn('2030 年沒有工時紀錄', self.get('/scoring?year=2030'))


if __name__ == '__main__':
    unittest.main()
