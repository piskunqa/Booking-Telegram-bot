from datetime import datetime
import shutil
from os import makedirs, listdir, rmdir, remove
from os.path import splitext, join, isdir, basename, exists

import flask_admin as admin
from flask import Flask, Response, redirect, request, flash
from flask_admin import AdminIndexView
from flask_admin.actions import action
from flask_admin.babel import lazy_gettext, gettext, ngettext
from flask_admin.contrib.peewee import ModelView
from flask_basicauth import BasicAuth
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from wtforms import FileField
from wtforms.validators import ValidationError, Length
from datetime import date

from config import admin_password, admin_user, admin_secret_key, allow_ip_list, lock_by_ip, UPLOAD_BASE
from models import Resource, Images, Booking

app = Flask(__name__)
app.config['SECRET_KEY'] = admin_secret_key
app.config['BASIC_AUTH_USERNAME'] = admin_user
app.config['BASIC_AUTH_PASSWORD'] = admin_password
basic_auth = BasicAuth(app)

makedirs(UPLOAD_BASE, exist_ok=True)


@app.route('/')
def index():
    return '<script>document.location.href = "/admin"</script>'


class DashboardView(AdminIndexView):
    """
        Custom admin dashboard view for Flask-Admin.

        This class customizes visibility and access control for the admin dashboard.
        It integrates HTTP Basic Authentication and optional IP-based restrictions.
    """

    def is_visible(self):
        """
            Determine whether this view should be visible in the Flask-Admin menu.

            Returns:
                bool: False to hide the dashboard link from the menu.
        """
        return False

    def is_accessible(self):
        """
            Determine whether the current user can access the dashboard.

            Performs authentication using HTTP Basic Auth and optional IP restriction.

            Raises:
                AuthException: If the user is not authenticated or their IP is not allowed.

            Returns:
                bool: True if access is granted.
        """
        if not basic_auth.authenticate():
            raise AuthException('Not authenticated.')
        else:
            if lock_by_ip:
                if request.remote_addr not in allow_ip_list:
                    raise AuthException('Not authenticated.')
                else:
                    return True
            else:
                return True

    def inaccessible_callback(self, name, **kwargs):
        """
            Callback invoked when a user tries to access the view but is denied.

            Redirects the user to the Basic Auth login prompt.

            Args:
                name (str): The name of the view.
                **kwargs: Additional keyword arguments passed by Flask-Admin.

            Returns:
                werkzeug.wrappers.Response: A redirect response to trigger Basic Auth challenge.
        """
        return redirect(basic_auth.challenge())


class AuthException(HTTPException):
    """
        Custom HTTP exception used for authentication failures.

        This exception is raised when a user fails to authenticate, either
        due to incorrect credentials or IP restriction. It automatically
        returns an HTTP 401 Unauthorized response with a Basic Auth challenge.
    """

    def __init__(self, message):
        """
            Initialize the AuthException.

            Args:
                message (str): A descriptive message explaining the authentication failure.

            Behavior:
                - Calls the parent HTTPException constructor.
                - Returns an HTTP 401 response with the body:
                    "You could not be authenticated. Please refresh the page."
                - Includes the 'WWW-Authenticate' header to trigger Basic Auth login prompt.
        """
        super().__init__(message, Response(
            "You could not be authenticated. Please refresh the page.", 401,
            {'WWW-Authenticate': 'Basic realm="Login Required"'}))


class SecureView(ModelView):
    """
        Custom Flask-Admin ModelView with authentication and optional IP restriction.

        This class extends the standard ModelView to enforce access control
        using HTTP Basic Authentication and an optional allowed IP list.
        It can be used as a base class for all admin models that require security.
    """

    def is_accessible(self):
        """
            Determine whether the current user can access this model view.

            Performs authentication using HTTP Basic Auth and, if enabled,
            checks whether the request originates from an allowed IP address.

            Raises:
                AuthException: If the user fails authentication or their IP is not allowed.

            Returns:
                bool: True if the user is authenticated and, if IP restriction is active,
                      their IP is in the allow list.
        """
        if not basic_auth.authenticate():
            raise AuthException('Not authenticated.')
        else:
            if lock_by_ip:
                if request.remote_addr not in allow_ip_list:
                    raise AuthException('Not authenticated.')
                else:
                    return True
            else:
                return True

    def inaccessible_callback(self, name, **kwargs):
        """
            Callback invoked when a user tries to access the view but is denied.

            Redirects the user to the Basic Auth login prompt.

            Args:
                name (str): The name of the view being accessed.
                **kwargs: Additional keyword arguments provided by Flask-Admin.

            Returns:
                werkzeug.wrappers.Response: A redirect response triggering the Basic Auth challenge.
        """
        return redirect(basic_auth.challenge())


class SecureImageView(SecureView):
    """
        Admin view for managing Images with file upload support.

        This class extends SecureView to provide:
        - Multiple image upload support on creation
        - Automatic filename numbering per resource
        - File type validation and upload limits
        - Hidden upload field during editing
        - Automatic file deletion when a record is removed

        Attributes:
            form_columns (list[str]): Columns to display in the form.
            form_extra_fields (dict): Additional custom form fields, including 'file_upload'.
            form_excluded_columns (list[str]): Columns excluded from the form.
            column_list (list[str]): Columns displayed in the list view.
    """
    form_columns = ['resource', 'file_upload']
    form_extra_fields = {
        'file_upload': FileField(
            'Upload Image',
            render_kw={"accept": ".png,.jpg,.jpeg,.gif", "multiple": True}
        )
    }
    form_excluded_columns = ['filename', 'created']
    column_list = ['id', 'resource', 'filename', 'created']

    def edit_form(self, obj=None):
        """
            Returns the form for editing an existing record.

            The 'file_upload' field is removed during editing, so no new files
            can be added while editing.

            Args:
                obj: The model instance being edited (optional).

            Returns:
                Flask-WTF form object with 'file_upload' field removed.
        """
        form = super().edit_form(obj)
        if 'file_upload' in form._fields:
            del form._fields['file_upload']
        return form

    def on_model_change(self, form, model, is_created):
        """
            Hook called when a model is created or updated.

            Handles multiple file uploads when creating a new record:
            - Validates that at least one file is uploaded
            - Limits the total number of files per resource to 5
            - Automatically assigns sequential numeric filenames
            - Saves files to a folder named after the resource ID

            Args:
                form: The submitted form.
                model: The model instance being created or updated.
                is_created (bool): True if the model is being created.

            Raises:
                ValidationError: If no files are uploaded or limits are exceeded.
        """
        if not is_created:
            return
        files = request.files.getlist("file_upload")
        files_with_names = [f for f in files if f.filename]
        if not files_with_names:
            raise ValidationError("Вы должны добавить файл")
        if len(files_with_names) > 5:
            raise ValidationError("Вы можете добавить до 5 файлов")
        resource_folder = join(UPLOAD_BASE, str(model.resource.id))
        exists_count = len(listdir(resource_folder)) if exists(resource_folder) else 0
        if exists_count + len(files_with_names) > 5:
            raise ValidationError(f"Вы можете добавить до 5 файла, сейчас к записи #{model.resource.id} прикреплено "
                                  f"{exists_count} файлов, вы можете добавить еще {5 - exists_count} файлов")
        for uploaded in files:
            ext = splitext(secure_filename(uploaded.filename))[1].lower().replace('.', '')
            makedirs(resource_folder, exist_ok=True)
            existing_files = listdir(resource_folder)
            existing_numbers = [int(splitext(f)[0]) for f in existing_files if f.split('.')[0].isdigit()]
            next_number = max(existing_numbers, default=0) + 1
            filename = f"{next_number}.{ext}"
            file_path = join(resource_folder, filename)
            uploaded.save(file_path)
            if model.filename:
                model_cls = type(model)
                model_cls.create(resource=model.resource, filename=filename)
            else:
                model.filename = filename

    @staticmethod
    def delete_file(model):
        """
            Safely delete the file associated with a model instance.

            Deletes the actual file on disk and removes the resource folder
            if it becomes empty. Uses basename checks to prevent path traversal.

            Args:
                model: The Images model instance.

            Side Effects:
                Flash messages are displayed for warnings or errors.
        """
        try:
            filename = getattr(model, 'filename', None)
            resource_obj = getattr(model, 'resource', None)
            if resource_obj is None:
                resource_id = None
            else:
                try:
                    resource_id = int(getattr(resource_obj, 'id', resource_obj))
                except Exception:
                    resource_id = str(resource_obj)
            if filename:
                safe_name = basename(filename)
                if safe_name != filename:
                    flash("Имя файла некорректно — файл на диске не будет удалён.", "warning")
                else:
                    file_path = join(UPLOAD_BASE, str(resource_id), filename)
                    if exists(file_path):
                        try:
                            remove(file_path)
                        except Exception as e:
                            flash(f"Не удалось удалить файл {filename}: {e}", "warning")
                    folder = join(UPLOAD_BASE, str(resource_id))
                    try:
                        if isdir(folder) and not listdir(folder):
                            rmdir(folder)
                    except Exception:
                        pass
        except Exception as exc:
            flash(f"Не удалось удалить файл: {exc}", "error")

    def delete_model(self, model):
        """
            Delete a model instance along with its associated file.

            Args:
                model: The Images model instance to delete.

            Returns:
                bool: Result of the parent delete_model call.
        """
        self.delete_file(model)
        return super().delete_model(model)

    @action(
        "delete",
        lazy_gettext("Удалить"),
        lazy_gettext("Вы уверены что хотите удалить выбранные записи?"),
    )
    def action_delete(self, ids):
        """
            Bulk delete action for selected records.

            Deletes each selected model along with its associated file
            and calls on_model_delete hook for each.

            Args:
                ids (list[int]): List of primary key IDs of selected records.

            Side Effects:
                Flash messages indicate success or failure.
        """
        try:
            model_pk = getattr(self.model, self._primary_key)
            count = 0
            query = self.model.select().filter(model_pk << ids)
            for m in query:
                self.delete_file(m)
                self.on_model_delete(m)
                m.delete_instance(recursive=True)
                count += 1
            flash(
                ngettext(
                    "Записи удалены.",
                    "%(count)s удалено успешно.",
                    count,
                    count=count,
                ),
                "success",
            )
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Ошибка при удаление записей. %(error)s", error=str(ex)),
                    "error",
                )


class ResourceView(SecureView):
    """
        Admin view for managing Resource objects.

        This class extends SecureView to provide:
        - Custom form columns for Resource
        - Automatic update of 'updated' timestamp on edits
        - Deletion of all associated image folders when a resource is deleted
        - Bulk deletion support with safety and flash messages

        Attributes:
            form_columns (list[str]): Columns displayed in the form.
            column_list (list[str]): Columns displayed in the list view.
    """
    form_columns = ['location', 'price', 'description', 'status']
    column_list = ['id', 'location', 'price', 'status', 'created', 'updated']

    form_args = {
        'description': {
            'validators': [Length(max=500, message="Максимум 500 символов")]
        }
    }

    def on_model_change(self, form, model, is_created):
        """
            Hook called when a model is created or updated.

            Updates the 'updated' timestamp for edited resources.

            Args:
                form: The submitted form.
                model: The Resource instance being created or updated.
                is_created (bool): True if the model is being created.
        """
        if not is_created:
            model.updated = datetime.now()

    @staticmethod
    def delete_folder(model):
        """
            Delete the folder containing all images for this resource.

            Args:
                model: The Resource instance.

            Side Effects:
                - Deletes the folder "images/{resource.id}" and all its contents.
                - Flash messages indicate warnings or errors during deletion.
        """
        try:
            folder = join(UPLOAD_BASE, str(model.id))
            if exists(folder) and isdir(folder):
                try:
                    shutil.rmtree(folder)
                except Exception as e:
                    flash(f"Ошибка при удалении папки с файлами: {e}", "warning")
        except Exception as exc:
            flash(f"Ошибка при удалении ресурса: {exc}", "error")

    def delete_model(self, model):
        """
            Delete a Resource instance along with its associated image folder.

            Args:
                model: The Resource instance to delete.

            Returns:
                bool: Result of the parent delete_model call.
        """
        self.delete_folder(model)
        return super().delete_model(model)

    @action(
        "delete",
        lazy_gettext("Удалить"),
        lazy_gettext("Вы уверены что хотите удалить выбранные записи?"),
    )
    def action_delete(self, ids):
        """
            Bulk delete action for selected Resource records.

            Deletes each selected resource along with its associated image folder
            and calls on_model_delete hook for each.

            Args:
                ids (list[int]): List of primary key IDs of selected records.

            Side Effects:
                Flash messages indicate success or failure of deletions.
        """
        try:
            model_pk = getattr(self.model, self._primary_key)
            count = 0
            query = self.model.select().filter(model_pk << ids)
            for m in query:
                self.delete_folder(m)
                self.on_model_delete(m)
                m.delete_instance(recursive=True)
                count += 1
            flash(
                ngettext(
                    "Записи удалены.",
                    "%(count)s удалено успешно.",
                    count,
                    count=count,
                ),
                "success",
            )
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Ошибка при удаление записей. %(error)s", error=str(ex)),
                    "error",
                )


class BookingAdmin(ModelView):
    """
        Admin view for managing Booking records.

        This class extends Flask-Admin's ModelView to enforce rules on deletion:
        - Individual deletion is blocked if the booking is confirmed and the check-out date is in the future.
        - Bulk deletion skips bookings that are confirmed with a future check-out date and provides feedback via flash messages.

        Methods:
            delete_model(model): Override to enforce deletion rules on single records.
            action_delete(ids): Override to enforce deletion rules on bulk-selected records.
    """

    def delete_model(self, model):
        """
            Delete a single booking record, enforcing business rules.

            Prevents deletion if:
                - status is "confirmed"
                - check_out is set and in the future

            Args:
                model: The Booking model instance to delete.

            Raises:
                Exception: If the booking cannot be deleted due to its status or check-out date.

            Returns:
                bool: Result of the parent delete_model call.
        """
        if model.status == "confirmed" and model.check_out and model.check_out > date.today():
            raise Exception("Нельзя удалить подтвержденное бронирование с актуальной датой выезда.")
        return super().delete_model(model)

    @action(
        "delete",
        lazy_gettext("Удалить"),
        lazy_gettext("Вы уверены что хотите удалить выбранные записи?"),
    )
    def action_delete(self, ids):
        """
            Bulk delete action for selected booking records.

            Skips bookings that are confirmed with a future check-out date.
            Provides feedback on how many were deleted and how many were skipped.

            Args:
                ids (list[int]): List of primary key IDs of selected booking records.

            Side Effects:
                Flash messages indicate the number of records successfully deleted
                and the number of records skipped due to business rules.
        """
        try:
            model_pk = getattr(self.model, self._primary_key)
            count = 0
            query = self.model.select().filter(model_pk << ids)
            not_deleted = 0
            for m in query:
                if m.status == "confirmed" and m.check_out and m.check_out > date.today():
                    not_deleted += 1
                    continue
                self.on_model_delete(m)
                m.delete_instance(recursive=True)
                count += 1
            flash(
                ngettext(
                    "Записи удалены.",
                    "%(count)s удалено успешно, %(not_deleted)s не удалено удалено, так как они ене используются.",
                    count,
                    count=count,
                    not_deleted=not_deleted,
                ),
                "success",
            )
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Ошибка при удаление записей. %(error)s", error=str(ex)),
                    "error",
                )


admin = admin.Admin(app, name='Bot Admin', index_view=DashboardView())
admin.add_view(ResourceView(Resource))
admin.add_view(SecureImageView(Images))
admin.add_view(BookingAdmin(Booking))
