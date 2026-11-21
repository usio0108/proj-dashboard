from collections import defaultdict
from datetime import date

from flask import flash, redirect, render_template, request, session, url_for
from ..extensions import db
from ..models import Personnel, Project, Setting, Task

MAX_WEIGHT = 1000


def get_rates():
    """Overtime and night multipliers from the Setting table; default 1 when unset or unreadable."""
    vals = {r.key: r.value for r in Setting.query.filter(Setting.key.in_(('overtime_rate', 'night_rate')))}

    def rate(key):
        try:
            return float(vals[key])
        except (KeyError, ValueError):
            return 1.0
    return rate('overtime_rate'), rate('night_rate')


def register(app):
    # Admin-only POST; CSRF protection is the app-wide SameSite=Lax cookie, same as the manage routes.
    @app.route('/scoring/weight/<int:id>', methods=['POST'])
    def set_project_weight(id):
        year = request.form.get('year', type=int)
        back = redirect(url_for('scoring', year=year, _anchor=f'project-{id}'))
        if not session.get('db_admin_auth'):
            flash('僅管理者可調整專案權重', 'error')
            return back
        project = Project.query.get_or_404(id)
        try:
            weight = float(request.form.get('weight', '').strip())
            if not 0 < weight <= MAX_WEIGHT:  # also rejects nan and inf; the cap keeps scores finite
                raise ValueError
        except ValueError:
            flash(f'權重必須是大於 0、不超過 {MAX_WEIGHT} 的數字', 'error')
            return back
        project.weight = weight
        db.session.commit()
        return back

    @app.route('/scoring')
    def scoring():
        this_year = date.today().year
        try:
            year = int(request.args.get('year', this_year))
            start, end = date(year, 1, 1), date(year + 1, 1, 1)
        except (ValueError, OverflowError):
            year = this_year
            start, end = date(year, 1, 1), date(year + 1, 1, 1)
        tasks = Task.query.filter(Task.date >= start, Task.date < end).all()
        weights = {p.id: p.weight for p in Project.query.all()}

        ot_rate, night_rate = get_rates()

        people = {p.name: p for p in Personnel.query.all()}
        # Roster: not resigned before this year, plus anyone with hours (even if resigned).
        names = {n for n, p in people.items() if p.resigned_date is None or p.resigned_date >= start}
        names |= {t.personnel for t in tasks}

        def row(name):
            person = people.get(name)
            return {'name': name, 'display': (person and person.display_name) or name,
                    'avatar': person.avatar_filename if person else None,
                    'resigned': bool(person and person.resigned_date), 'score': 0.0}

        roster = sorted((row(n) for n in names), key=lambda r: (r['display'], r['name']))
        by_name = {r['name']: r for r in roster}
        score = defaultdict(float)  # (project_id, name) -> weighted score
        hours = defaultdict(float)  # project_id -> raw hours (not weighted)
        for t in tasks:
            s = ((t.day_hours or 0) + (t.overtime_hours or 0) * ot_rate
                 + (t.night_hours or 0) * night_rate) * weights.get(t.project_id, 1)
            score[t.project_id, t.personnel] += s
            by_name[t.personnel]['score'] += s
            hours[t.project_id] += t.total_hours

        projects = [
            {'id': p.id, 'name': p.name, 'total': hours[p.id], 'weight': p.weight,
             'cells': [score.get((p.id, r['name']), 0.0) for r in roster]}
            for p in Project.query.order_by(Project.name).all() if p.id in hours
        ]
        return render_template('scoring.html', year=year, this_year=this_year, projects=projects, roster=roster)
