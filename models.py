from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# ----------------------------
# Authentication User Loader
# ----------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ----------------------------
# System User / Admin Model
# ----------------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default='staff')
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


# ----------------------------
# Student Model
# ----------------------------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    
    activities = db.relationship('LabActivity', backref='student', lazy=True)

    def __repr__(self):
        return f'<Student {self.name}>'


# ----------------------------
# Teacher Model 
# ----------------------------
class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    
    activities = db.relationship(
        'LabActivity',
        backref='teacher',
        lazy=True,
        foreign_keys='LabActivity.teacher_id'
    )

    def __repr__(self):
        return f'<Teacher {self.name}>'


# ----------------------------
# Laboratory Model
# ----------------------------
class Laboratory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lab_name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    
    activities = db.relationship('LabActivity', backref='laboratory', lazy=True)

    def __repr__(self):
        return f'<Laboratory {self.lab_name}>'


# ----------------------------
# Material Model
# ----------------------------
class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)  
    brand = db.Column(db.String(50), nullable=True)       
    quantity = db.Column(db.Integer, default=1)
    status = db.Column(db.String(30), default='working')  
    
    laboratory_id = db.Column(db.Integer, db.ForeignKey('laboratory.id'), nullable=False)
    laboratory = db.relationship('Laboratory', backref=db.backref('materials', lazy=True))
    
    activities = db.relationship('LabActivity', backref='material', lazy=True)

    def __repr__(self):
        return f'<Material {self.material_name}>'


# ----------------------------
# Combined Activity & Lending Log
# ----------------------------
class LabActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_type = db.Column(db.String(30), nullable=False) # 'attendance' or 'lending'
    user_type = db.Column(db.String(20), nullable=False)     # 'student' or 'teacher'
    date_logged = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Cleaned Lending Dates
    lending_date = db.Column(db.DateTime, nullable=True)
    return_date = db.Column(db.DateTime, nullable=True)
    return_status = db.Column(db.String(20), default='N/A')  # 'borrowed', 'returned', or 'N/A'
    
    attendance_purpose = db.Column(db.String(50), default='N/A') 
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    subject = db.Column(db.String(120), nullable=True)
    
    # Foreign Keys
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    teaching_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    laboratory_id = db.Column(db.Integer, db.ForeignKey('laboratory.id'), nullable=True)
    material_id = db.Column(db.Integer, db.ForeignKey('material.id'), nullable=True)

    teaching_teacher = db.relationship(
        'Teacher',
        foreign_keys=[teaching_teacher_id],
        backref=db.backref('teaching_sessions', lazy=True)
    )

    def __repr__(self):
        return f'<LabActivity {self.activity_type} - {self.user_type}>'


# ----------------------------
# Maintenance Model
# ----------------------------
class Maintenance(db.Model):
    __tablename__ = 'maintenance'
    
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey('material.id'), nullable=False)
    issue_description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Pending')  # Pending, Completed
    date_reported = db.Column(db.DateTime, default=datetime.utcnow)

    material = db.relationship('Material', backref=db.backref('maintenance_records', lazy=True))

    def __repr__(self):
        return f'<Maintenance Item {self.material_id} - {self.status}>'


# ----------------------------
# Notification Model
# ----------------------------
class Notification(db.Model):
    __tablename__ = 'notification'

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(30), nullable=False)  # 'maintenance', 'overdue', 'session_ended'
    message = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(100), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Notification {self.type}: {self.message[:40]}>'


# ----------------------------
# System Configuration Model
# ----------------------------
class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(500), nullable=True)

    def __repr__(self):
        return f'<SystemConfig {self.key}={self.value}>'


# ----------------------------
# Audit Log Model
# ----------------------------
class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action_type = db.Column(db.String(30), nullable=False)  # login, create, update, delete, error
    details = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(20), default='success')    # success, failed
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('audit_logs', lazy=True))

    def __repr__(self):
        return f'<AuditLog {self.action_type} by user {self.user_id}>'
