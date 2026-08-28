from pathlib import Path

from django import forms


ALLOWED_EVIDENCE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_EVIDENCE_SIZE = 5 * 1024 * 1024


class EmployeeClarificationForm(forms.Form):
    explanation = forms.CharField(
        label="المبرر",
        min_length=5,
        max_length=3000,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5}),
    )
    evidence = forms.FileField(
        label="رفع شاهد (اختياري)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png"}),
        help_text="PDF أو صورة، بحد أقصى 5 ميجابايت.",
    )

    def clean_evidence(self):
        evidence = self.cleaned_data.get("evidence")
        if not evidence:
            return None
        if evidence.size > MAX_EVIDENCE_SIZE:
            raise forms.ValidationError("حجم الشاهد يتجاوز 5 ميجابايت.")
        if Path(evidence.name).suffix.lower() not in ALLOWED_EVIDENCE_EXTENSIONS:
            raise forms.ValidationError("نوع الملف غير مسموح.")
        content_type = getattr(evidence, "content_type", "")
        if content_type not in {"application/pdf", "image/jpeg", "image/png"}:
            raise forms.ValidationError("محتوى الملف غير مسموح.")
        signature = evidence.read(8)
        evidence.seek(0)
        valid_signature = (
            (content_type == "application/pdf" and signature.startswith(b"%PDF-"))
            or (content_type == "image/jpeg" and signature.startswith(b"\xff\xd8\xff"))
            or (content_type == "image/png" and signature == b"\x89PNG\r\n\x1a\n")
        )
        if not valid_signature:
            raise forms.ValidationError("محتوى الملف لا يطابق نوعه.")
        return evidence


class ClarificationReviewForm(forms.Form):
    decision = forms.ChoiceField(
        label="القرار",
        choices=(("approve", "اعتماد"), ("return", "إعادة للاستكمال"), ("reject", "رفض")),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    comment = forms.CharField(
        label="التعليق",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
