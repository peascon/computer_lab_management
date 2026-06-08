from app import app, db
from flask import render_template, request, redirect, url_for, flash, Response
from app.models import Student, Laboratory, Material, Teacher, LabActivity, Maintenance, User, Notification, AuditLog, SystemConfig
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse

EMAIL_PATTERN = '@'
MATERIAL_STATUSES = {'Available', 'Low Stock', 'Out of Order'}
MATERIAL_CATEGORIES = {'Equipment', 'Computer', 'Other'}
ACTIVITY_TYPES = {'attendance', 'lending'}
USER_TYPES = {'student', 'teacher'}

# ----------------------------
# CORE UTILITIES & HELPERS (Placed first so routes can safely call them)
# ----------------------------
def clean_form_value(name):
    return request.form.get(name, '').strip()


def parse_int(value, field_name, minimum=None):
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        flash(f'{field_name} must be a valid number.', 'danger')
        return None

    if minimum is not None and parsed_value < minimum:
        flash(f'{field_name} must be at least {minimum}.', 'danger')
        return None

    return parsed_value


def parse_time(value, field_name):
    try:
        return datetime.strptime(value, '%H:%M').time()
    except (TypeError, ValueError):
        flash(f'{field_name} must be a valid time.', 'danger')
        return None


def is_valid_email(email):
    import re
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$', email))


def handle_commit_success(redirect_endpoint):
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('That record conflicts with existing data. Please check unique fields like email.', 'danger')
        return None

    flash('Changes saved successfully.', 'success')
    return redirect(url_for(redirect_endpoint))


def commit_or_flash(message='Changes saved successfully.'):
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('This action could not be completed because related records still depend on it.', 'danger')
        return False

    flash(message, 'success')
    return True


def validate_person_form(model, current_id=None):
    name = clean_form_value('name')
    department = clean_form_value('department')
    email = clean_form_value('email').lower()

    if not name:
        flash('Name is required.', 'danger')
    if not department:
        flash('Department is required.', 'danger')
    if not email or not is_valid_email(email):
        flash('Enter a valid email address.', 'danger')

    duplicate = model.query.filter_by(email=email).first() if email else None
    if duplicate and duplicate.id != current_id:
        flash('That email address is already registered.', 'danger')

    if not name or not department or not email or not is_valid_email(email) or (duplicate and duplicate.id != current_id):
        return None

    return {'name': name, 'department': department, 'email': email}


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            return "Admin access required.", 403

        return view(*args, **kwargs)

    return wrapped_view




# ----------------------------
# Notification Helper
# ----------------------------
def create_notification(type, message, link=None):
    n = Notification(type=type, message=message, link=link)
    db.session.add(n)
    # don't commit here — caller commits as part of their transaction


def get_unread_count():
    return Notification.query.filter_by(is_read=False).count()

@app.before_request
def ensure_user_role_column():
    if app.config.get('USER_ROLE_SCHEMA_CHECKED'):
        return

    rows = db.session.execute(db.text("PRAGMA table_info(user)")).fetchall()
    columns = [row[1] for row in rows]

    if columns and 'role' not in columns:
        db.session.execute(db.text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'staff'"))
        db.session.execute(db.text("UPDATE user SET role = 'admin' WHERE username = 'admin'"))
        db.session.commit()

    activity_rows = db.session.execute(db.text("PRAGMA table_info(lab_activity)")).fetchall()
    activity_columns = [row[1] for row in activity_rows]

    if activity_columns:
        attendance_columns = {
            'start_time': 'TIME',
            'end_time': 'TIME',
            'subject': 'VARCHAR(120)',
            'teaching_teacher_id': 'INTEGER',
        }

        for column_name, column_type in attendance_columns.items():
            if column_name not in activity_columns:
                db.session.execute(db.text(f"ALTER TABLE lab_activity ADD COLUMN {column_name} {column_type}"))

        db.session.commit()

    app.config['USER_ROLE_SCHEMA_CHECKED'] = True



@app.context_processor
def inject_notifications():
    from flask_login import current_user
    if current_user.is_authenticated:
        unread_count = Notification.query.filter_by(is_read=False).count()
        recent = Notification.query.order_by(Notification.created_at.desc()).limit(5).all()
        return dict(unread_count=unread_count, recent_notifications=recent)
    return dict(unread_count=0, recent_notifications=[])

# ----------------------------
# Authentication Routes
# ----------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = clean_form_value('username')
        password = clean_form_value('password')

        if not username or not password:
            return render_template('login.html', error="Username and password are required")

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            
            next_page = request.args.get('next')
            if not next_page or urlparse(next_page).netloc != '':
                next_page = url_for('home')
                
            return redirect(next_page)
        else:
            return render_template('login.html', error="Invalid username or password")

    # Handles the initial GET request when loading the page
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))


# ----------------------------
# Home / Dashboard
# ----------------------------
@app.route('/')
@login_required
def home():
    student_count = Student.query.count()
    teacher_count = Teacher.query.count()
    laboratory_count = Laboratory.query.count()
    material_count = Material.query.count()
    activity_count = LabActivity.query.count()

    active_loans = LabActivity.query.filter_by(activity_type='lending', return_status='borrowed').count()
    pending_repairs = Maintenance.query.filter_by(status='Pending').count()

    return render_template(
        'index.html',
        student_count=student_count,
        teacher_count=teacher_count,
        laboratory_count=laboratory_count,
        material_count=material_count,
        activity_count=activity_count,
        active_loans=active_loans,       
        pending_repairs=pending_repairs   
    )# ----------------------------
# System Administrator Settings
# ----------------------------
@app.route('/admin/settings')
@login_required
@admin_required
def admin_settings():
    import os

    # System config dict
    config_rows = SystemConfig.query.all()
    config = {row.key: row.value for row in config_rows}
    # Coerce boolean strings
    for bool_key in ('enable_notifications', 'enable_loans', 'require_approval'):
        config[bool_key] = config.get(bool_key, 'true').lower() == 'true'
    config.setdefault('max_loan_days', 7)

    # Users
    admins = User.query.filter_by(role='admin').all()
    non_admin_users = User.query.filter(User.role != 'admin').all()

    # Audit logs
    audit_logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()

    # System health
    import time
    db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace('sqlite:///', '')
    db_size = '—'
    if db_path and os.path.exists(db_path):
        size_bytes = os.path.getsize(db_path)
        db_size = f'{size_bytes / 1024:.1f} KB' if size_bytes < 1024*1024 else f'{size_bytes / (1024*1024):.2f} MB'

    errors_24h = AuditLog.query.filter(
        AuditLog.status == 'failed',
        AuditLog.timestamp >= datetime.utcnow() - timedelta(hours=24)
    ).count()

    system_uptime = '—'
    try:
        import psutil
        uptime_seconds = time.time() - psutil.boot_time()
        hours, rem = divmod(int(uptime_seconds), 3600)
        minutes, _ = divmod(rem, 60)
        system_uptime = f'{hours}h {minutes}m'
    except ImportError:
        system_uptime = 'N/A (install psutil)'

    system_counts = {
        'students': Student.query.count(),
        'teachers': Teacher.query.count(),
        'laboratories': Laboratory.query.count(),
        'materials': Material.query.count(),
        'activity_logs': LabActivity.query.count(),
        'maintenance_records': Maintenance.query.count(),
    }

    # Backups list from instance folder
    backup_dir = os.path.join(os.path.dirname(app.instance_path), 'backups')
    backups = []
    if os.path.exists(backup_dir):
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            if fname.endswith('.csv'):
                fpath = os.path.join(backup_dir, fname)
                stat = os.stat(fpath)
                size_kb = stat.st_size / 1024
                backups.append({
                    'filename': fname,
                    'created_at': datetime.fromtimestamp(stat.st_mtime),
                    'size': f'{size_kb:.1f} KB',
                    'id': fname  # use filename as id
                })

    return render_template(
        'admin_settings.html',
        config=config,
        admins=admins,
        non_admin_users=non_admin_users,
        audit_logs=audit_logs,
        system_uptime=system_uptime,
        db_size=db_size,
        active_sessions=1,
        errors_24h=errors_24h,
        backups=backups,
        system_counts=system_counts,
    )


@app.route('/admin/save-config', methods=['POST'])
@login_required
@admin_required
def save_system_config():
    fields = ['system_name', 'system_email', 'org_name', 'contact_phone', 'max_loan_days']
    toggles = ['enable_notifications', 'enable_loans', 'require_approval']

    for field in fields:
        val = request.form.get(field, '').strip()
        row = SystemConfig.query.filter_by(key=field).first()
        if row:
            row.value = val
        else:
            db.session.add(SystemConfig(key=field, value=val))

    for toggle in toggles:
        val = 'true' if request.form.get(toggle) else 'false'
        row = SystemConfig.query.filter_by(key=toggle).first()
        if row:
            row.value = val
        else:
            db.session.add(SystemConfig(key=toggle, value=val))

    db.session.add(AuditLog(user_id=current_user.id, action_type='update', details='System configuration updated', status='success'))
    db.session.commit()
    flash('Configuration saved successfully.', 'success')
    return redirect(url_for('admin_settings') + '#system-config')


@app.route('/admin/grant-admin', methods=['POST'])
@login_required
@admin_required
def grant_admin():
    user_id = request.form.get('user_id', type=int)
    user = User.query.get_or_404(user_id)
    user.role = 'admin'
    db.session.add(AuditLog(user_id=current_user.id, action_type='update', details=f'Granted admin to {user.username}', status='success'))
    db.session.commit()
    flash(f'{user.username} has been granted admin privileges.', 'success')
    return redirect(url_for('admin_settings') + '#users-management')


@app.route('/admin/revoke-admin/<int:id>', methods=['POST'])
@login_required
@admin_required
def revoke_admin(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('You cannot revoke your own admin privileges.', 'danger')
        return redirect(url_for('admin_settings') + '#users-management')
    user.role = 'staff'
    db.session.add(AuditLog(user_id=current_user.id, action_type='update', details=f'Revoked admin from {user.username}', status='success'))
    db.session.commit()
    flash(f'Admin privileges revoked from {user.username}.', 'warning')
    return redirect(url_for('admin_settings') + '#users-management')


@app.route('/admin/create-backup', methods=['POST'])
@login_required
@admin_required
def create_backup():
    import os, csv, io

    backup_dir = os.path.join(os.path.dirname(app.instance_path), 'backups')
    os.makedirs(backup_dir, exist_ok=True)

    output = io.StringIO()
    writer = csv.writer(output)

    # Students
    writer.writerow(['--- STUDENTS ---'])
    writer.writerow(['ID', 'Name', 'Department', 'Email'])
    for s in Student.query.all():
        writer.writerow([s.id, s.name, s.department, s.email])

    writer.writerow([])

    # Teachers
    writer.writerow(['--- TEACHERS ---'])
    writer.writerow(['ID', 'Name', 'Department', 'Email'])
    for t in Teacher.query.all():
        writer.writerow([t.id, t.name, t.department, t.email])

    writer.writerow([])

    # Materials
    writer.writerow(['--- MATERIALS ---'])
    writer.writerow(['ID', 'Name', 'Category', 'Brand', 'Quantity', 'Status', 'Lab ID'])
    for m in Material.query.all():
        writer.writerow([m.id, m.material_name, m.category, m.brand, m.quantity, m.status, m.laboratory_id])

    writer.writerow([])

    # Activity Logs
    writer.writerow(['--- ACTIVITY LOGS ---'])
    writer.writerow(['ID', 'Type', 'User Type', 'Date', 'Student ID', 'Teacher ID', 'Material ID', 'Return Status'])
    for a in LabActivity.query.all():
        writer.writerow([a.id, a.activity_type, a.user_type, a.date_logged, a.student_id, a.teacher_id, a.material_id, a.return_status])

    writer.writerow([])

    # Maintenance
    writer.writerow(['--- MAINTENANCE ---'])
    writer.writerow(['ID', 'Material ID', 'Issue', 'Status', 'Date Reported'])
    for r in Maintenance.query.all():
        writer.writerow([r.id, r.material_id, r.issue_description, r.status, r.date_reported])

    csv_content = output.getvalue()
    filename = f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    filepath = os.path.join(backup_dir, filename)

    with open(filepath, 'w', newline='') as f:
        f.write(csv_content)

    db.session.add(AuditLog(user_id=current_user.id, action_type='create', details=f'Backup created: {filename}', status='success'))
    db.session.commit()

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/admin/download-backup/<path:id>')
@login_required
@admin_required
def download_backup(id):
    import os
    backup_dir = os.path.join(os.path.dirname(app.instance_path), 'backups')
    filepath = os.path.join(backup_dir, id)
    if not os.path.exists(filepath):
        flash('Backup file not found.', 'danger')
        return redirect(url_for('admin_settings') + '#backup-restore')
    with open(filepath, 'r') as f:
        content = f.read()
    return Response(content, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={id}'})


@app.route('/admin/delete-backup/<path:id>', methods=['POST'])
@login_required
@admin_required
def delete_backup(id):
    import os
    backup_dir = os.path.join(os.path.dirname(app.instance_path), 'backups')
    filepath = os.path.join(backup_dir, id)
    if os.path.exists(filepath):
        os.remove(filepath)
        db.session.add(AuditLog(user_id=current_user.id, action_type='delete', details=f'Backup deleted: {id}', status='success'))
        db.session.commit()
        flash('Backup deleted.', 'success')
    else:
        flash('Backup file not found.', 'danger')
    return redirect(url_for('admin_settings') + '#backup-restore')


@app.route('/admin/restore-backup', methods=['POST'])
@login_required
@admin_required
def restore_backup():
    flash('Restore from backup is not implemented in this version. Import your CSV manually.', 'warning')
    return redirect(url_for('admin_settings') + '#backup-restore')


@app.route('/admin/clear-cache', methods=['POST'])
@login_required
@admin_required
def clear_cache():
    db.session.add(AuditLog(user_id=current_user.id, action_type='update', details='Cache cleared', status='success'))
    db.session.commit()
    flash('Cache cleared successfully.', 'success')
    return redirect(url_for('admin_settings') + '#system-health')


@app.route('/admin/reset-stats', methods=['POST'])
@login_required
@admin_required
def reset_stats():
    db.session.add(AuditLog(user_id=current_user.id, action_type='delete', details='System statistics reset', status='success'))
    db.session.commit()
    flash('Statistics reset. Note: activity log records are preserved.', 'info')
    return redirect(url_for('admin_settings') + '#system-health')


# ----------------------------
# Student Routes
# ----------------------------
@app.route('/create-student', methods=['GET', 'POST'])
@login_required
def create_student():
    if request.method == 'POST':
        form_data = validate_person_form(Student)
        if not form_data:
            return render_template('create_student.html')

        student = Student(**form_data)
        db.session.add(student)
        response = handle_commit_success('students')
        if response:
            return response

    return render_template('create_student.html')


@app.route('/students')
@login_required
def students():
    all_students = Student.query.all()
    return render_template('students.html', students=all_students)


@app.route('/edit-student/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    student = Student.query.get_or_404(id)

    if request.method == 'POST':
        form_data = validate_person_form(Student, current_id=student.id)
        if not form_data:
            return render_template('edit_student.html', student=student)

        student.name = form_data['name']
        student.department = form_data['department']
        student.email = form_data['email']
        response = handle_commit_success('students')
        if response:
            return response

    return render_template('edit_student.html', student=student)


@app.route('/delete-student/<int:id>', methods=['POST'])
@login_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    db.session.delete(student)
    commit_or_flash('Student removed successfully.')
    return redirect(url_for('students'))


# ----------------------------
# Laboratory Routes
# ----------------------------
@app.route('/laboratories')
@login_required
def laboratories():
    all_labs = Laboratory.query.all()
    return render_template('laboratories.html', laboratories=all_labs)


@app.route('/create-laboratory', methods=['GET', 'POST'])
@login_required
def create_laboratory():
    if request.method == 'POST':
        lab_name = clean_form_value('lab_name')
        location = clean_form_value('location')
        capacity = parse_int(clean_form_value('capacity'), 'Capacity', minimum=1)

        if not lab_name:
            flash('Lab name is required.', 'danger')
        if not location:
            flash('Location is required.', 'danger')
        if not lab_name or not location or capacity is None:
            return render_template('create_laboratory.html')

        lab = Laboratory(
            lab_name=lab_name,
            location=location,
            capacity=capacity
        )
        db.session.add(lab)
        response = handle_commit_success('laboratories')
        if response:
            return response

    return render_template('create_laboratory.html')


@app.route('/edit-laboratory/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_laboratory(id):
    lab = Laboratory.query.get_or_404(id)

    if request.method == 'POST':
        lab_name = clean_form_value('lab_name')
        location = clean_form_value('location')
        capacity = parse_int(clean_form_value('capacity'), 'Capacity', minimum=1)

        if not lab_name:
            flash('Lab name is required.', 'danger')
        if not location:
            flash('Location is required.', 'danger')
        if not lab_name or not location or capacity is None:
            return render_template('edit_laboratory.html', lab=lab)

        lab.lab_name = lab_name
        lab.location = location
        lab.capacity = capacity
        response = handle_commit_success('laboratories')
        if response:
            return response

    return render_template('edit_laboratory.html', lab=lab)


@app.route('/delete-laboratory/<int:id>', methods=['POST'])
@login_required
def delete_laboratory(id):
    lab = Laboratory.query.get_or_404(id)
    db.session.delete(lab)
    commit_or_flash('Laboratory removed successfully.')
    return redirect(url_for('laboratories'))


# ----------------------------
# Material Routes
# ----------------------------
@app.route('/materials')
@login_required
def materials():
    all_materials = Material.query.all()
    return render_template('materials.html', materials=all_materials)


@app.route('/create-material', methods=['GET', 'POST'])
@login_required
def create_material():
    labs = Laboratory.query.all()
    
    if request.method == 'POST':
        material_name = clean_form_value('material_name')
        category = clean_form_value('category')
        brand = clean_form_value('brand') or None
        quantity = parse_int(clean_form_value('quantity'), 'Quantity', minimum=0)
        status = clean_form_value('status')
        laboratory_id = parse_int(clean_form_value('laboratory_id'), 'Laboratory', minimum=1)

        if not material_name:
            flash('Material name is required.', 'danger')
        if category not in MATERIAL_CATEGORIES:
            flash('Choose a valid material category.', 'danger')
        if status not in MATERIAL_STATUSES:
            flash('Choose a valid material status.', 'danger')
        if laboratory_id is not None and not Laboratory.query.get(laboratory_id):
            flash('Choose an existing laboratory.', 'danger')
            laboratory_id = None

        if not material_name or category not in MATERIAL_CATEGORIES or status not in MATERIAL_STATUSES or quantity is None or laboratory_id is None:
            return render_template('create_material.html', labs=labs)

        material = Material(
            material_name=material_name,
            category=category,
            brand=brand,
            quantity=quantity,
            status=status,
            laboratory_id=laboratory_id
        )
        db.session.add(material)
        response = handle_commit_success('materials')
        if response:
            return response

    return render_template('create_material.html', labs=labs)


@app.route('/edit-material/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_material(id):
    material = Material.query.get_or_404(id)
    labs = Laboratory.query.all()

    if request.method == 'POST':
        material_name = clean_form_value('material_name')
        category = clean_form_value('category')
        brand = clean_form_value('brand') or None
        quantity = parse_int(clean_form_value('quantity'), 'Quantity', minimum=0)
        status = clean_form_value('status')
        laboratory_id = parse_int(clean_form_value('laboratory_id'), 'Laboratory', minimum=1)

        if not material_name:
            flash('Material name is required.', 'danger')
        if category not in MATERIAL_CATEGORIES:
            flash('Choose a valid material category.', 'danger')
        if status not in MATERIAL_STATUSES:
            flash('Choose a valid material status.', 'danger')
        if laboratory_id is not None and not Laboratory.query.get(laboratory_id):
            flash('Choose an existing laboratory.', 'danger')
            laboratory_id = None

        if not material_name or category not in MATERIAL_CATEGORIES or status not in MATERIAL_STATUSES or quantity is None or laboratory_id is None:
            return render_template('edit_material.html', material=material, labs=labs)

        material.material_name = material_name
        material.category = category
        material.brand = brand
        material.quantity = quantity
        material.status = status
        material.laboratory_id = laboratory_id
        
        response = handle_commit_success('materials')
        if response:
            return response

    return render_template('edit_material.html', material=material, labs=labs)


@app.route('/delete-material/<int:id>', methods=['POST'])
@login_required
def delete_material(id):
    material = Material.query.get_or_404(id)
    db.session.delete(material)
    commit_or_flash('Material removed successfully.')
    return redirect(url_for('materials'))


# ----------------------------
# Teacher Routes
# ----------------------------
@app.route('/teachers')
@login_required
def teachers():
    all_teachers = Teacher.query.all()
    return render_template('teachers.html', teachers=all_teachers)


@app.route('/create-teacher', methods=['GET', 'POST'])
@login_required
def create_teacher():
    if request.method == 'POST':
        form_data = validate_person_form(Teacher)
        if not form_data:
            return render_template('create_teacher.html')

        teacher = Teacher(**form_data)
        db.session.add(teacher)
        response = handle_commit_success('teachers')
        if response:
            return response
    return render_template('create_teacher.html')


@app.route('/edit-teacher/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_teacher(id):
    teacher = Teacher.query.get_or_404(id)

    if request.method == 'POST':
        form_data = validate_person_form(Teacher, current_id=teacher.id)
        if not form_data:
            return render_template('edit_teacher.html', teacher=teacher)

        teacher.name = form_data['name']
        teacher.department = form_data['department']
        teacher.email = form_data['email']
        response = handle_commit_success('teachers')
        if response:
            return response

    return render_template('edit_teacher.html', teacher=teacher)


@app.route('/delete-teacher/<int:id>', methods=['POST'])
@login_required
def delete_teacher(id):
    teacher = Teacher.query.get_or_404(id)
    db.session.delete(teacher)
    commit_or_flash('Teacher removed successfully.')
    return redirect(url_for('teachers'))


# ----------------------------
# Lab Attendance & Lending Routes
# ----------------------------
@app.route('/activity-log')
@login_required
def activity_log():
    logs = LabActivity.query.order_by(LabActivity.date_logged.desc()).all()
    return render_template('activity_log.html', logs=logs)


@app.route('/log-activity', methods=['GET', 'POST'])
@login_required
def log_activity():
    students = Student.query.all()
    teachers = Teacher.query.all()
    labs = Laboratory.query.all()
    materials = Material.query.filter(Material.quantity > 0).all()

    if request.method == 'POST':
        user_selection = clean_form_value('user_target')
        activity_type = clean_form_value('activity_type')

        try:
            user_type, raw_user_id = user_selection.split('-', 1)
        except ValueError:
            user_type = None
            raw_user_id = None

        user_id = parse_int(raw_user_id, 'Person', minimum=1)

        if user_type not in USER_TYPES:
            flash('Choose a valid student or teacher.', 'danger')
        if activity_type not in ACTIVITY_TYPES:
            flash('Choose a valid activity type.', 'danger')
        if user_type == 'student' and user_id is not None and not Student.query.get(user_id):
            flash('Choose an existing student.', 'danger')
            user_id = None
        if user_type == 'teacher' and user_id is not None and not Teacher.query.get(user_id):
            flash('Choose an existing teacher.', 'danger')
            user_id = None
        if user_type not in USER_TYPES or activity_type not in ACTIVITY_TYPES or user_id is None:
            return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)
        
        log = LabActivity(
            activity_type=activity_type,
            user_type=user_type,
            student_id=user_id if user_type == 'student' else None,
            teacher_id=user_id if user_type == 'teacher' else None
        )
        
        if activity_type == 'attendance':
            laboratory_id = parse_int(clean_form_value('laboratory_id'), 'Laboratory', minimum=1)
            start_time = parse_time(clean_form_value('start_time'), 'Start time')
            end_time = parse_time(clean_form_value('end_time'), 'End time')

            if laboratory_id is None or not Laboratory.query.get(laboratory_id):
                flash('Choose an existing laboratory for attendance.', 'danger')
                return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)
            if start_time is None or end_time is None:
                return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)
            if end_time <= start_time:
                flash('End time must be later than start time.', 'danger')
                return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)

            log.laboratory_id = laboratory_id
            log.attendance_purpose = clean_form_value('attendance_purpose') or 'Research'
            log.start_time = start_time
            log.end_time = end_time

            if log.attendance_purpose == 'Learning with Teacher':
                subject = clean_form_value('subject')
                teaching_teacher_id = parse_int(clean_form_value('teaching_teacher_id'), 'Teaching teacher', minimum=1)

                if not subject:
                    flash('Subject is required for teaching sessions.', 'danger')
                if teaching_teacher_id is None or not Teacher.query.get(teaching_teacher_id):
                    flash('Choose the teacher who will teach the subject.', 'danger')
                    teaching_teacher_id = None
                if not subject or teaching_teacher_id is None:
                    return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)

                log.subject = subject
                log.teaching_teacher_id = teaching_teacher_id
                # Notify session ended
                teacher_obj = Teacher.query.get(teaching_teacher_id)
                teacher_name = teacher_obj.name if teacher_obj else 'Unknown teacher'
                create_notification(
                    type='session_ended',
                    message=f'Teaching session ended: {subject} by {teacher_name} ({start_time.strftime("%H:%M")} – {end_time.strftime("%H:%M")})',
                    link='/activity-log'
                )
            
        elif activity_type == 'lending':
            material_id = parse_int(clean_form_value('material_id'), 'Material', minimum=1)
            mat = Material.query.get(material_id) if material_id is not None else None

            if not mat:
                flash('Choose an existing material to lend.', 'danger')
                return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)
            if mat.quantity <= 0:
                flash('This material is out of stock and cannot be lent out.', 'danger')
                return render_template('log_activity.html', students=students, teachers=teachers, labs=labs, materials=materials)

            log.material_id = material_id
            log.attendance_purpose = 'N/A'
            
            log.lending_date = datetime.now()
            log.return_date = datetime.now() + timedelta(days=7) 
            log.return_status = 'borrowed'
            mat.quantity -= 1
            # Notify that item has been lent out
            borrower_name = Student.query.get(user_id).name if user_type == 'student' else Teacher.query.get(user_id).name
            create_notification(
                type='lending',
                message=f'{mat.material_name} lent to {borrower_name} — due back in 7 days.',
                link='/activity-log'
            )

        db.session.add(log)
        response = handle_commit_success('activity_log')
        if response:
            return response

    return render_template(
        'log_activity.html', 
        students=students, 
        teachers=teachers, 
        labs=labs, 
        materials=materials
    )


@app.route('/return-material/<int:id>', methods=['POST'])
@login_required
def return_material(id):
    log = LabActivity.query.get_or_404(id)
    
    if log.activity_type == 'lending' and log.return_status == 'borrowed':
        log.return_status = 'returned'
        
        mat = Material.query.get(log.material_id)
        if mat:
            mat.quantity += 1
            
        commit_or_flash('Material marked as returned.')
        
    return redirect(url_for('activity_log'))


# ----------------------------
# Maintenance Routes
# ----------------------------
@app.route('/maintenance')
@login_required
def maintenance():
    records = Maintenance.query.order_by(Maintenance.date_reported.desc()).all()
    return render_template('maintenance.html', records=records)


@app.route('/report-maintenance', methods=['GET', 'POST'])
@login_required
def report_maintenance():
    materials = Material.query.all()

    if request.method == 'POST':
        material_id = parse_int(clean_form_value('material_id'), 'Material', minimum=1)
        issue_description = clean_form_value('issue_description')

        if material_id is None or not Material.query.get(material_id):
            flash('Choose an existing material.', 'danger')
            material_id = None
        if not issue_description:
            flash('Issue description is required.', 'danger')
        if material_id is None or not issue_description:
            return render_template('report_maintenance.html', materials=materials)

        record = Maintenance(
            material_id=material_id,
            issue_description=issue_description,
            status='Pending'
        )
        db.session.add(record)
        mat = Material.query.get(material_id)
        mat_name = mat.material_name if mat else f'Material #{material_id}'
        create_notification(
            type='maintenance',
            message=f'Equipment issue reported: {mat_name} — {issue_description[:80]}',
            link='/maintenance'
        )
        response = handle_commit_success('maintenance')
        if response:
            return response

    return render_template('report_maintenance.html', materials=materials)


@app.route('/complete-maintenance/<int:id>', methods=['POST'])
@login_required
def complete_maintenance(id):
    record = Maintenance.query.get_or_404(id)
    record.status = 'Completed'
    commit_or_flash('Maintenance record marked as completed.')
    return redirect(url_for('maintenance'))


# ----------------------------
# Analytics & Reports Route
# ----------------------------
@app.route('/reports')
@login_required
def reports():
    from sqlalchemy import and_, extract

    now = datetime.now()
    one_week_ago = now - timedelta(days=7)
    one_month_ago = now - timedelta(days=30)

    # --- Flat metric counts (monthly) ---
    monthly_attendance = LabActivity.query.filter(
        LabActivity.activity_type == 'attendance',
        LabActivity.date_logged >= one_month_ago
    ).count()

    monthly_loans = LabActivity.query.filter(
        LabActivity.activity_type == 'lending',
        LabActivity.date_logged >= one_month_ago
    ).count()

    monthly_returns = LabActivity.query.filter(
        LabActivity.return_status == 'returned',
        LabActivity.date_logged >= one_month_ago
    ).count()

    monthly_broken = Maintenance.query.filter(
        Maintenance.date_reported >= one_month_ago
    ).count()

    # --- Weekly counts ---
    weekly_attendance = LabActivity.query.filter(
        LabActivity.activity_type == 'attendance',
        LabActivity.date_logged >= one_week_ago
    ).count()

    weekly_loans = LabActivity.query.filter(
        LabActivity.activity_type == 'lending',
        LabActivity.date_logged >= one_week_ago
    ).count()

    weekly_returns = LabActivity.query.filter(
        LabActivity.return_status == 'returned',
        LabActivity.date_logged >= one_week_ago
    ).count()

    weekly_broken = Maintenance.query.filter(
        Maintenance.date_reported >= one_week_ago
    ).count()

    # --- Overdue loans ---
    overdue_loans = LabActivity.query.filter(
        LabActivity.activity_type == 'lending',
        LabActivity.return_status == 'borrowed',
        LabActivity.return_date < now
    ).all()

    # --- Attendance purpose breakdown ---
    purpose_distribution = db.session.query(
        LabActivity.attendance_purpose,
        func.count(LabActivity.id)
    ).filter(
        LabActivity.activity_type == 'attendance'
    ).group_by(LabActivity.attendance_purpose).all()

    # --- Chart data: last 7 days by day ---
    monthly_chart_labels = []
    monthly_chart_attendance = []
    monthly_chart_loans = []

    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        label = day.strftime('%a %d')
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)

        att = LabActivity.query.filter(
            LabActivity.activity_type == 'attendance',
            LabActivity.date_logged >= day_start,
            LabActivity.date_logged <= day_end
        ).count()

        loans = LabActivity.query.filter(
            LabActivity.activity_type == 'lending',
            LabActivity.date_logged >= day_start,
            LabActivity.date_logged <= day_end
        ).count()

        monthly_chart_labels.append(label)
        monthly_chart_attendance.append(att)
        monthly_chart_loans.append(loans)

    # --- Detailed records for the report tables ---
    attendance_records = LabActivity.query.filter(
        LabActivity.activity_type == 'attendance'
    ).order_by(LabActivity.date_logged.desc()).all()

    lending_records = LabActivity.query.filter(
        LabActivity.activity_type == 'lending'
    ).order_by(LabActivity.date_logged.desc()).all()

    maintenance_records = Maintenance.query.order_by(
        Maintenance.date_reported.desc()
    ).all()

    return render_template(
        'reports.html',
        monthly_attendance=monthly_attendance,
        monthly_loans=monthly_loans,
        monthly_returns=monthly_returns,
        monthly_broken=monthly_broken,
        weekly_attendance=weekly_attendance,
        weekly_loans=weekly_loans,
        weekly_returns=weekly_returns,
        weekly_broken=weekly_broken,
        overdue_loans=overdue_loans,
        purpose_distribution=purpose_distribution,
        monthly_chart_labels=monthly_chart_labels,
        monthly_chart_attendance=monthly_chart_attendance,
        monthly_chart_loans=monthly_chart_loans,
        attendance_records=attendance_records,
        lending_records=lending_records,
        maintenance_records=maintenance_records,
        now=now,
    )

# ----------------------------
# Password Reset (token-based, no email required)
# ----------------------------
import hmac

# In-memory store: {token: {'user_id': int, 'expires': datetime}}
_reset_tokens = {}


def _make_token(user_id):
    import secrets as _secrets
    token = _secrets.token_urlsafe(32)
    _reset_tokens[token] = {
        'user_id': user_id,
        'expires': datetime.utcnow() + timedelta(hours=1)
    }
    return token


def _consume_token(token):
    entry = _reset_tokens.get(token)
    if not entry:
        return None
    if datetime.utcnow() > entry['expires']:
        _reset_tokens.pop(token, None)
        return None
    _reset_tokens.pop(token, None)
    return entry['user_id']


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        user = User.query.filter_by(username=username).first()

        # Always show same message to avoid username enumeration
        flash(
            'If that username exists, a reset link has been generated. '
            'Copy the link below and open it in your browser.',
            'info'
        )

        if user:
            token = _make_token(user.id)
            reset_url = url_for('reset_password', token=token, _external=True)
            flash(f'Reset link (valid 1 hour): {reset_url}', 'warning')

        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    entry = _reset_tokens.get(token)
    if not entry or datetime.utcnow() > entry['expires']:
        flash('This reset link is invalid or has expired.', 'danger')
        return redirect(url_for('forgot_password'))

    user = User.query.get(entry['user_id'])
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('reset_password.html', token=token, username=user.username)
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token, username=user.username)

        _consume_token(token)
        user.set_password(password)
        db.session.commit()
        flash('Password updated successfully. Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token, username=user.username)


# ----------------------------
# Notifications Routes
# ----------------------------
@app.route('/notifications')
@login_required
def notifications():
    # Auto-generate overdue notifications for any borrowed items past return date
    overdue_loans = LabActivity.query.filter(
        LabActivity.activity_type == 'lending',
        LabActivity.return_status == 'borrowed',
        LabActivity.return_date < datetime.now()
    ).all()

    for loan in overdue_loans:
        # Avoid duplicate overdue notifications
        existing = Notification.query.filter_by(
            type='overdue',
            is_read=False
        ).filter(Notification.message.contains(f'#{loan.id}')).first()

        if not existing:
            borrower = loan.student.name if loan.user_type == 'student' else loan.teacher.name
            mat = loan.material.material_name if loan.material else 'Unknown item'
            days_overdue = (datetime.now() - loan.return_date).days
            create_notification(
                type='overdue',
                message=f'OVERDUE (log #{loan.id}): {mat} borrowed by {borrower} is {days_overdue} day(s) overdue.',
                link='/activity-log'
            )

    if overdue_loans:
        db.session.commit()

    all_notifications = Notification.query.order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=all_notifications)


@app.route('/notifications/mark-read/<int:id>', methods=['POST'])
@login_required
def mark_notification_read(id):
    n = Notification.query.get_or_404(id)
    n.is_read = True
    db.session.commit()
    return redirect(url_for('notifications'))


@app.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(is_read=False).update({'is_read': True})
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('notifications'))


@app.route('/notifications/delete/<int:id>', methods=['POST'])
@login_required
def delete_notification(id):
    n = Notification.query.get_or_404(id)
    db.session.delete(n)
    db.session.commit()
    return redirect(url_for('notifications'))
