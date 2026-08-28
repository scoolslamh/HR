from django.test import TestCase

from .models import AuditLog, AuditLogImmutableError


class AuditLogImmutabilityTests(TestCase):
    def setUp(self):
        self.audit_log = AuditLog.objects.create(
            action="accounts.user_created",
            module="accounts",
            outcome=AuditLog.Outcome.SUCCESS,
        )

    def test_instance_and_queryset_updates_are_rejected(self):
        original_action = self.audit_log.action
        self.audit_log.action = "accounts.user_changed"

        with self.assertRaises(AuditLogImmutableError):
            self.audit_log.save()

        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.audit_log.pk).update(
                action="accounts.user_changed"
            )

        self.audit_log.refresh_from_db()
        self.assertEqual(self.audit_log.action, original_action)

    def test_instance_and_queryset_deletes_are_rejected(self):
        with self.assertRaises(AuditLogImmutableError):
            self.audit_log.delete()

        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.audit_log.pk).delete()

        self.assertTrue(AuditLog.objects.filter(pk=self.audit_log.pk).exists())
