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
        self.assertRegex(table, r'<th[^>]*>專案</th>')
        self.assertEqual(self.headers(table), ['專案', 'Bob', '小愛'])   # fixed order by display name
        rows = self.rows(table)
        self.assertEqual(len(rows), 2)               # one row per project
        self.assertIn(f'href="/project/{p1}"', rows[0])
        self.assertIn('總工時 15.5 小時', rows[0])     # P1: 8 + 2 + 1.5 + 4
        self.assertIn(f'href="/employee-case?person=Alice&amp;project_id={p1}"', rows[0])
        self.assertEqual(self.cells(rows[0]), ['4', '11.5'])
        self.assertIn(f'href="/project/{p2}"', rows[1])
        self.assertEqual(self.cells(rows[1]), ['–', '3'])   # Bob has nothing on P2
        self.assertNotIn('100', table)               # 2024 hours stay out of the 2025 scope

    @staticmethod
    def rows(table):
        return re.findall(r'<tr id="project-\d+".*?</tr>', table, re.S)

    @staticmethod
    def cells(row):
        return [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td class="px-4 py-3 text-right.*?</td>', row, re.S)]

    @staticmethod
    def headers(table):
        return [re.sub(r'<[^>]+>', ' ', h).split()[0] for h in re.findall(r'<th.*?</th>', table, re.S)]

    def overview_cards(self, html):
        overview = html[:html.index('id="scoring-table"')]
        return re.findall(r'text-ink-muted">([^<]+)(?: <span[^>]*>已離職</span>)?</div>\s*<div[^>]*><span[^>]*>([\d.]+)</span>', overview)

    def test_overview_totals_cross_projects(self):
        self.seed()
        self.assertEqual(self.overview_cards(self.get('/scoring?year=2025')), [('Bob', '4'), ('小愛', '14.5')])   # 11.5 + 3

    def set_rates(self, ot, night):
        return self.admin_client().post('/manage-db/rates', data={'overtime_rate': ot, 'night_rate': night})

    def test_rates_default_to_one_and_apply_to_score_not_hours(self):
        p1, _ = self.seed()
        self.assertEqual(self.overview_cards(self.get('/scoring?year=2025'))[1], ('小愛', '14.5'))
        self.assertEqual(self.set_rates('2', '3').status_code, 302)
        html = self.get('/scoring?year=2025')
        # Alice: P1 = 8 + 2*2 + 1.5*3 = 16.5, P2 = 3; Bob: 4 day hours
        self.assertEqual(self.overview_cards(html), [('Bob', '4'), ('小愛', '19.5')])
        table = re.search(r'<table id="scoring-table".*?</table>', html, re.S).group(0)
        self.assertEqual(self.cells(self.rows(table)[0]), ['4', '16.5'])
        self.assertIn('總工時 15.5 小時', table)       # raw hours stay unweighted

    def test_rates_validated_and_admin_only(self):
        from core.routes.scoring import get_rates
        self.set_rates('2', '3')
        admin = self.admin_client()
        for bad in (('0', '1'), ('1', '-1'), ('abc', '1'), ('1', ''), ('nan', '1'), ('1', 'inf'), ('1000.5', '1')):
            resp = admin.post('/manage-db/rates', data={'overtime_rate': bad[0], 'night_rate': bad[1]}, follow_redirects=True)
            self.assertIn('倍率必須是大於 0', resp.get_data(as_text=True), bad)
        self.app.test_client().post('/manage-db/rates', data={'overtime_rate': '9', 'night_rate': '9'})
        with self.app.app_context():
            self.assertEqual(get_rates(), (2.0, 3.0))   # all rejected; anonymous ignored
        self.assertIn('name="overtime_rate"', admin.get('/manage-db').get_data(as_text=True))

    def test_roster_includes_resigned_with_hours_excludes_resigned_before_year(self):
        from core.models import Personnel, Project, Task
        self.seed()
        with self.app.app_context():
            p = Project.query.filter_by(name='P2').one()
            self.db.session.add_all([
                Personnel(name='Gone', display_name='離職有工時', resigned_date=date(2025, 6, 1)),
                Personnel(name='Old', display_name='早離職', resigned_date=date(2024, 12, 31)),
                Personnel(name='Idle', display_name='在職無工時'),
                Task(personnel='Gone', project_id=p.id, date=date(2025, 2, 1), day_hours=5, description='g'),
            ])
            self.db.session.commit()
        html = self.get('/scoring?year=2025')
        table = re.search(r'<table id="scoring-table".*?</table>', html, re.S).group(0)
        self.assertEqual(self.headers(table), ['專案', 'Bob', '在職無工時', '小愛', '離職有工時'])
        self.assertEqual(html.count('已離職'), 2)      # card + column header of Gone only
        self.assertNotIn('早離職', html)
        self.assertEqual(self.cells(self.rows(table)[1]), ['–', '–', '3', '5'])
        self.assertEqual([c[0] for c in self.overview_cards(html)], ['Bob', '在職無工時', '小愛', '離職有工時'])
        self.assertEqual(self.overview_cards(html)[1], ('在職無工時', '0'))
        self.assertIn('早離職', self.get('/scoring?year=2024'))   # not yet resigned on Jan 1 2024

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
        rows = self.rows(table)
        self.assertIn('總工時 15.5 小時', rows[0])    # raw hours, unweighted
        self.assertEqual(self.cells(rows[0]), ['6', '17.25'])
        self.assertEqual(self.overview_cards(html), [('Bob', '6'), ('小愛', '20.25')])   # 17.25 + 3
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
