from datetime import datetime

from peewee import Model, IntegerField, BooleanField, DateTimeField, TextField, CharField, FloatField, ForeignKeyField, \
    DateField

from config import DATABASE, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD
from utils import MySQLDatabaseReconnected

db = MySQLDatabaseReconnected(DATABASE, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)


class BaseModel(Model):
    class Meta:
        database = db


class Resource(BaseModel):
    """Model for objects available for booking"""
    id: int
    location = CharField(null=False)
    price = FloatField(null=False)
    description = TextField(null=True)
    status = IntegerField(default=1)
    created = DateTimeField(default=datetime.now)
    updated = DateTimeField(default=datetime.now)

    def __str__(self):
        return f"{self.id}: {self.location}"


class Images(BaseModel):
    """Model for images"""
    id: int
    filename = TextField(null=False)
    resource = ForeignKeyField(Resource, null=False)
    created = DateTimeField(default=datetime.now)

class Booking(BaseModel):
    """Model for booking"""
    id: int
    telegram_id = IntegerField(null=False)
    resource = ForeignKeyField(Resource, null=False)
    check_in = DateField(null=True)
    check_out = DateField(null=True)
    created = DateTimeField(default=datetime.now)
    status = CharField(default='pending')
    amount = FloatField(default=0)

db.create_tables([Resource, Images, Booking], safe=True)
