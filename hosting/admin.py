from django.conf import settings
from django.contrib import admin
from django.contrib.admin.utils import display_for_value
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.admin import GenericTabularInline
# from django.contrib.gis import admin as gis_admin
from django.contrib.gis.db.models import PointField
from django.core.cache import cache
from django.db import models
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import ugettext_lazy as _

from django_countries.fields import Country

from maps.widgets import MapboxGlWidget

from .admin_utils import (
    CountryMentionedOnlyFilter, EmailValidityFilter,
    PlaceHasLocationFilter, ProfileHasUserFilter, ShowConfirmedMixin,
    ShowDeletedMixin, SupervisorFilter, VisibilityTargetFilter,
)
from .models import (
    Condition, ContactPreference, Phone, Place,
    Preferences, Profile, VisibilitySettings, Website,
)
from .widgets import AdminImageWithPreviewWidget

admin.site.index_template = 'admin/custom_index.html'
admin.site.disable_action('delete_selected')

admin.site.unregister(User)
admin.site.unregister(Group)


class PlaceInLine(ShowConfirmedMixin, ShowDeletedMixin, admin.StackedInline):
    model = Place
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        'country', 'state_province', ('city', 'closest_city'), 'postcode', 'address',
        'location',
        'description', 'short_description',
        ('max_guest', 'max_night', 'contact_before'), 'conditions',
        'available', 'in_book', ('tour_guide', 'have_a_drink'), 'sporadic_presence',
        'display_confirmed', 'is_deleted',
    )
    raw_id_fields = ('owner', 'family_members', 'authorized_users',)  # 'checked_by')
    readonly_fields = ('display_confirmed', 'is_deleted',)
    fk_name = 'owner'
    classes = ('collapse',)


class PhoneInLine(ShowDeletedMixin, admin.TabularInline):
    model = Phone
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ('number', 'country', 'type', 'comments', 'confirmed_on', 'is_deleted')
    readonly_fields = ('confirmed_on', 'is_deleted',)
    fk_name = 'profile'


class VisibilityInLine(GenericTabularInline):
    model = VisibilitySettings
    extra = 0
    can_delete = False
    fields = ('visible_online_public', 'visible_online_authed', 'visible_in_book', 'id')
    ct_fk_field = 'model_id'

    def get_readonly_fields(self, request, obj=None):
        return self.fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PreferencesInLine(admin.StackedInline):
    model = Preferences
    extra = 0
    can_delete = False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        'id', 'username', 'email', 'password_algorithm', 'profile_link',
        'is_active', 'is_supervisor', 'last_login', 'date_joined',
        'is_staff', 'is_superuser',
    )
    list_display_links = ('id', 'username')
    list_select_related = ('profile',)
    list_filter = (
        SupervisorFilter, 'is_active', 'is_staff', 'is_superuser', EmailValidityFilter,
        ('groups', admin.RelatedOnlyFieldListFilter),
    )
    date_hierarchy = 'date_joined'

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('email',)}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        (_('Supervisors'), {'fields': ('groups',)}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    readonly_fields = ('date_joined',)

    def password_algorithm(self, obj):
        if len(obj.password) == 32:
            return _("MD5 (weak)")
        if obj.password[:13] == 'pbkdf2_sha256':
            return 'PBKDF2 SHA256'
    password_algorithm.short_description = _("Password algorithm")

    def profile_link(self, obj):
        try:
            fullname = obj.profile if (obj.profile.first_name or obj.profile.last_name) else "--."
            return format_html('<a href="{url}">{name}</a>', url=obj.profile.get_admin_url(), name=fullname)
        except AttributeError:
            return '[ - ]'
    profile_link.short_description = _("profile")

    def is_supervisor(self, obj):
        value = any(g for g in obj.groups.all() if len(g.name) == 2)
        return display_for_value(value, None, boolean=True)
    is_supervisor.short_description = _("supervisor status")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('groups')

    def get_field_queryset(self, db, db_field, request):
        if db_field.name == 'groups':
            return CustomGroupAdmin.CountryGroup.objects
        return super().get_field_queryset(db, db_field, request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Group)
class CustomGroupAdmin(GroupAdmin):
    list_display = ('name', 'country', 'supervisors')
    list_per_page = 50

    def country(self, obj):
        return Country(obj.name).name if len(obj.name) == 2 else "-"
    country.short_description = _("country")

    def supervisors(self, obj):
        def get_formatted_list():
            for u in obj.user_set.all():
                link = reverse('admin:auth_user_change', args=[u.pk])
                account_link = format_html('<a href="{url}">{username}</a>', url=link, username=u)
                is_deleted = not u.is_active
                try:
                    profile_link = format_html('<sup>(<a href="{url}">{name}</a>)</sup>',
                                               url=u.profile.get_admin_url(), name=_("profile"))
                except Profile.DoesNotExist:
                    profile_link = ''
                else:
                    is_deleted = is_deleted or u.profile.deleted_on
                indicator = '<span style="color:#dd4646">&#x2718;!</span>' if is_deleted else ''
                yield " ".join([indicator, account_link, profile_link])
        return format_html(", ".join(get_formatted_list()))
    supervisors.short_description = _("Supervisors")

    class CountryGroup(Group):
        class Meta:
            proxy = True
            permissions = (
                ("can_supervise", "Can modify users from specific country"),
            )

        def __str__(self):
            if len(self.name) != 2:
                return self.name
            return format_html('{country_code}&emsp;&ndash;&ensp;{country_name}',
                               country_code=self.name, country_name=Country(self.name).name)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('user_set__profile')


class TrackingModelAdmin(ShowConfirmedMixin):
    fields = (
        ('checked_on', 'checked_by'), 'display_confirmed', 'deleted_on',
    )
    readonly_fields = ('display_confirmed',)

    class InstanceApprover(User):
        class Meta:
            proxy = True

        def __str__(self):
            try:
                fullname = " : ".join([self.username, str(self.profile)])
            except Profile.DoesNotExist:
                fullname = self.username
            return " ".join([fullname, "[N/A]" if not self.is_active else ""])

    def get_field_queryset(self, db, db_field, request):
        if db_field.name == 'checked_by':
            from django.db.models import Q
            return (self.InstanceApprover.objects
                    .filter(Q(is_superuser=True) | Q(groups__name__regex=r'[A-Z]{2}'))
                    .distinct()
                    .select_related('profile').defer('profile__description')
                    .order_by('username'))
        return super().get_field_queryset(db, db_field, request)


@admin.register(VisibilitySettings)
class VisibilityAdmin(admin.ModelAdmin):
    visibility_fields = tuple(
        f.name for f in VisibilitySettings._meta.get_fields()
        if f.name.startswith(VisibilitySettings._PREFIX)
    )
    list_display = (
        'id', '__str__', 'content_object_link',
    ) + visibility_fields
    list_display_links = ('id', '__str__')
    search_fields = [
        'id', 'model_id',
    ]
    list_filter = (
        VisibilityTargetFilter,
        'visible_in_book', 'visible_online_authed', 'visible_online_public',
    )
    fields = (
        'model_type', 'content_object_link', 'content_type',
    ) + visibility_fields
    readonly_fields = fields

    def content_object_link(self, obj):
        try:
            link = reverse('admin:{content.app_label}_{content.model}_change'.format(content=obj.content_type),
                           args=[obj.model_id])
            return format_html(
                '{pk}: <a href="{url}">{content}</a>',
                url=link,
                pk=obj.content_object.pk, content=obj.content_object
            )
        except AttributeError:
            return '-'
    content_object_link.short_description = _("object")
    content_object_link.admin_order_field = 'model_id'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Profile)
class ProfileAdmin(TrackingModelAdmin, ShowDeletedMixin, admin.ModelAdmin):
    list_display = (
        'id', '__str__', 'title', 'first_name', 'last_name',
        'birth_date',  # 'avatar', 'description',
        'user__email', 'user_link',
        'confirmed_on', 'checked_by', 'is_deleted', 'modified',
    )
    list_display_links = ('id', '__str__')
    search_fields = [
        'id', 'first_name', 'last_name', 'user__email', 'user__username',
    ]
    list_filter = (
        'confirmed_on', 'checked_on', 'deleted_on', EmailValidityFilter, ProfileHasUserFilter,
    )
    date_hierarchy = 'birth_date'
    fields = (
        'user', 'title', 'first_name', 'last_name', 'names_inversed', 'birth_date',
        'description', 'avatar', 'email', 'supervisor',
    ) + TrackingModelAdmin.fields
    raw_id_fields = ('user',)  # 'checked_by')
    radio_fields = {'title': admin.HORIZONTAL}
    readonly_fields = ('supervisor',) + TrackingModelAdmin.readonly_fields
    formfield_overrides = {
        models.ImageField: {'widget': AdminImageWithPreviewWidget},
    }
    inlines = [VisibilityInLine, PreferencesInLine, PlaceInLine, ]
    # PhoneInLine]  # https://code.djangoproject.com/ticket/26819

    def user__email(self, obj):
        try:
            return obj.user.email
        except AttributeError:
            return '-'
    user__email.short_description = _("email address")
    user__email.admin_order_field = 'user__email'

    def user_link(self, obj):
        try:
            link = reverse('admin:auth_user_change', args=[obj.user.pk])
            return format_html('<a href="{url}">{username}</a>', url=link, username=obj.user)
        except AttributeError:
            return '-'
    user_link.short_description = _("user")
    user_link.admin_order_field = 'user'

    def supervisor(self, obj):
        country_list = CustomGroupAdmin.CountryGroup.objects.filter(user__pk=obj.user_id if obj.user_id else -1)
        if country_list:
            return format_html(',&nbsp; '.join(map(str, country_list)))
        else:
            return self.get_empty_value_display()
    supervisor.short_description = _("supervisor status")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'checked_by')


@admin.register(Place)
class PlaceAdmin(TrackingModelAdmin, ShowDeletedMixin, admin.ModelAdmin):
    list_display = (
        'city', 'postcode', 'state_province', 'display_country',
        'display_location',
        # 'max_host', 'max_night', 'contact_before',
        'available', 'in_book',
        'owner_link',
        'confirmed_on', 'checked_by', 'is_deleted', 'modified',
    )
    list_display_links = (
        'city', 'state_province', 'display_country',
    )
    search_fields = [
        'address', 'city', 'postcode', 'country', 'state_province',
        'owner__first_name', 'owner__last_name', 'owner__user__email',
    ]
    list_filter = (
        'confirmed_on', 'checked_on', 'in_book', 'available', PlaceHasLocationFilter, 'deleted_on',
        CountryMentionedOnlyFilter,
    )
    fields = (
        'owner', 'country', 'state_province', ('city', 'closest_city'), 'postcode', 'address',
        'location',
        'description', 'short_description',
        ('max_guest', 'max_night', 'contact_before'), 'conditions',
        'available', 'in_book', ('tour_guide', 'have_a_drink'), 'sporadic_presence',
        'family_members', 'authorized_users',
    ) + TrackingModelAdmin.fields
    formfield_overrides = {
        PointField: {'widget': MapboxGlWidget},
    }
    raw_id_fields = ('owner', 'authorized_users',)  # 'checked_by',)
    filter_horizontal = ('family_members',)
    inlines = [VisibilityInLine, ]

    def display_country(self, obj):
        return '{country.code}: {country.name}'.format(country=obj.country)
    display_country.short_description = _("country")
    display_country.admin_order_field = 'country'

    def display_location(self, obj):
        return '{point.y:.4f} {point.x:.4f}'.format(point=obj.location) if obj.location else None
    display_location.short_description = _("location")

    def owner_link(self, obj):
        return format_html('<a href="{url}">{name}</a>', url=obj.owner.get_admin_url(), name=obj.owner)
    owner_link.short_description = _("owner")
    owner_link.admin_order_field = 'owner'

    class FamilyMember(Profile):
        class Meta:
            ordering = ['first_name', 'last_name', 'id']
            proxy = True

        def __str__(self):
            return "(p:%05d, u:%05d) %s" % (self.pk,
                                            self.user_id if self.user_id else 0,
                                            super().__str__())

    def get_queryset(self, request):
        cached_qs = cache.get('PlaceQS:Req:'+request.path)
        if cached_qs:
            return cached_qs
        qs = super().get_queryset(request).select_related('owner__user', 'checked_by')
        qs = qs.defer('owner__description')
        try:
            if not self.single_object_view:
                qs = qs.defer('description', 'short_description')
        except Exception:
            pass
        cache.set('PlaceQS:Req:'+request.path, qs, 5)
        return qs

    def get_field_queryset(self, db, db_field, request):
        if db_field.name == 'family_members':
            return PlaceAdmin.FamilyMember.objects.defer('description').select_related('user')
        return super().get_field_queryset(db, db_field, request)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        self.single_object_view = True
        return super().change_view(
            request, object_id, form_url=form_url, extra_context=extra_context)

    def add_view(self, request, form_url='', extra_context=None):
        self.single_object_view = True
        return super().add_view(
            request, form_url=form_url, extra_context=extra_context)

    def changelist_view(self, request, extra_context=None):
        self.single_object_view = False
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(Phone)
class PhoneAdmin(TrackingModelAdmin, ShowDeletedMixin, admin.ModelAdmin):
    list_display = ('number_intl', 'profile_link', 'country_code', 'display_country', 'type', 'is_deleted')
    list_select_related = ('profile__user',)
    search_fields = ['number', 'country']
    list_filter = ('type', 'deleted_on', CountryMentionedOnlyFilter)
    fields = (
        'profile', 'number', 'country', 'type', 'comments',
    ) + TrackingModelAdmin.fields
    raw_id_fields = ('profile',)
    radio_fields = {'type': admin.VERTICAL}
    inlines = [VisibilityInLine, ]

    def number_intl(self, obj):
        return obj.number.as_international
    number_intl.short_description = _("number")
    number_intl.admin_order_field = 'number'

    def profile_link(self, obj):
        return format_html('<a href="{url}">{name}</a>', url=obj.profile.get_admin_url(), name=obj.profile)
    profile_link.short_description = _("profile")
    profile_link.admin_order_field = 'profile'

    def country_code(self, obj):
        return obj.number.country_code
    country_code.short_description = _("country code")

    def display_country(self, obj):
        return '{country.code}: {country.name}'.format(country=obj.country)
    display_country.short_description = _("country")
    display_country.admin_order_field = 'country'


@admin.register(Condition)
class ConditionAdmin(admin.ModelAdmin):
    list_display = ('name', 'abbr')
    prepopulated_fields = {'slug': ("name",)}

    fields = ('name', 'abbr', 'slug', 'latex', 'icon')

    def icon(self, obj):
        return format_html('<img src="{static}img/cond_{slug}.svg" style="width:4ex; height:4ex;"/>',
                           static=settings.STATIC_URL, slug=obj.slug)
    icon.short_description = _("image")

    def add_view(self, request, form_url='', extra_context=None):
        self.readonly_fields = ()
        return super().add_view(
            request, form_url=form_url, extra_context=extra_context)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        self.readonly_fields = ('icon',)
        return super().change_view(
            request, object_id, form_url=form_url, extra_context=extra_context)


@admin.register(Website)
class WebsiteAdmin(TrackingModelAdmin, admin.ModelAdmin):
    list_display = ('url', 'profile')
    search_fields = [
        'url',
        'profile__first_name', 'profile__last_name', 'profile__user__email', 'profile__user__username',
    ]
    fields = ('profile', 'url') + TrackingModelAdmin.fields
    raw_id_fields = ('profile',)


@admin.register(ContactPreference)
class ContactPreferenceAdmin(admin.ModelAdmin):
    pass
