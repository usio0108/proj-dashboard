from collections import defaultdict
from datetime import date

from flask import flash, redirect, render_template, request, session, url_for
from ..extensions import db
from ..models import Personnel, Project, Task

MAX_WEIGHT = 1000


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

        people = {p.name: p for p in Personnel.query.all()}

        def row(name):
            person = people.get(name)
            return {'name': name, 'display': (person and person.display_name) or name,
                    'avatar': person.avatar_filename if person else None,
                    'score': 0.0}

        per_project = defaultdict(dict)  # project_id -> employee -> row
        hours = defaultdict(float)       # project_id -> raw hours (not weighted)
        overall = {}
        for t in tasks:
            w = weights.get(t.project_id, 1)
            e = per_project[t.project_id].setdefault(t.personnel, row(t.personnel))
            e['score'] += t.total_hours * w  # score == hours * project weight
            hours[t.project_id] += t.total_hours
            total = overall.setdefault(t.personnel, row(t.personnel))
            total['score'] += t.total_hours * w

        by_score = lambda rows: sorted(rows, key=lambda r: (-r['score'], r['display']))
        projects = [
            {'id': p.id, 'name': p.name, 'total': hours[p.id], 'weight': p.weight,
             'employees': by_score(per_project[p.id].values())}
            for p in Project.query.order_by(Project.name).all() if p.id in per_project
        ]
        return render_template('scoring.html', year=year, this_year=this_year, projects=projects,
                               overall=by_score(overall.values()))
