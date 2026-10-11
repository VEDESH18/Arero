"""Template endpoints: built-in designs and custom company templates (logo + branding)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings
from app.api.docs import PDF_RESPONSE, error
from app.config import Settings
from app.database import get_db
from app.models import CustomTemplate
from app.schemas import CustomTemplateForm, TemplateDetail, TemplateOut
from app.services import custom_templates
from app.services.certificate_generator import TEMPLATES, Template, render_preview_pdf
from app.services.images import ImageRejectedError, ImageStore, ImageTooLargeError

router = APIRouter(prefix="/templates", tags=["1. Templates"])

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
NOT_FOUND_TEMPLATE = {404: error("Unknown template id.")}


def get_images(request: Request) -> ImageStore:
    return request.app.state.images


def template_form(
    name: Annotated[str, Form(description="Shown in the template list.", examples=["Acme Academy Blue"])],
    primary_color: Annotated[str, Form(description="Hex colour for title, name and border.", examples=["#1F3B6F"])],
    secondary_color: Annotated[str, Form(description="Hex colour for accents.", examples=["#C9A227"])],
    font: Annotated[str | None, Form(description="Helvetica (default), Times-Roman or Courier.")] = None,
    title_text: Annotated[str | None, Form(description="Heading; may use placeholders. Default: Certificate of Completion.")] = None,
    body_text: Annotated[
        str | None,
        Form(description="Must contain {{name}}. Placeholders: {{name}}, {{course}}, {{issuer}}, {{date}}."),
    ] = None,
    border_style: Annotated[str | None, Form(description="none, simple or double (default).")] = None,
    layout: Annotated[str | None, Form(description="landscape (default) or portrait.")] = None,
    signer_name: Annotated[str | None, Form(examples=["Dr. Maya Singh"])] = None,
    signer_title: Annotated[str | None, Form(examples=["Head of Training"])] = None,
) -> CustomTemplateForm:
    """Collect the multipart text fields and validate them with `CustomTemplateForm` (omitted fields use its defaults)."""
    values = {key: value for key, value in locals().items() if value is not None}
    try:
        return CustomTemplateForm(**values)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors])


@router.get("", response_model=list[TemplateOut], summary="List available templates")
def list_templates(request: Request, db: DbSession) -> list[TemplateOut]:
    """Built-in designs first, then custom ones (`type` tells them apart).
    Use a template's `id` as `certificate.template` when submitting a job. Open `preview_url` to see it.
    """
    builtin = [_builtin_out(t, request) for t in TEMPLATES.values()]
    custom = [_custom_out(t, request) for t in custom_templates.list_templates(db)]
    return builtin + custom


@router.post(
    "",
    status_code=201,
    response_model=TemplateDetail,
    summary="Create a custom template (logo and branding)",
    responses={
        413: error("An image is larger than MAX_UPLOAD_BYTES."),
        422: error("Invalid field (colour, font, placeholder, ...) or an image that is not a real PNG/JPEG."),
    },
)
def create_template(
    form: Annotated[CustomTemplateForm, Depends(template_form)],
    logo: Annotated[UploadFile, File(description="PNG or JPEG, up to 2 MB. Printed at the top of the certificate.")],
    request: Request,
    db: DbSession,
    images: Annotated[ImageStore, Depends(get_images)],
    signature: Annotated[UploadFile | None, File(description="Optional PNG or JPEG signature, drawn above the signer line.")] = None,
) -> TemplateDetail:
    """Send as **multipart/form-data**. The returned `id` can be used as `certificate.template` in **POST /api/v1/jobs**.

    Images are checked by decoding them (the file name and type header are ignored), downscaled if large,
    and stored under random names.
    """
    logo_bytes = _read_upload(logo, images.max_bytes)
    signature_bytes = _read_upload(signature, images.max_bytes) if signature is not None and signature.filename else None
    try:
        template = custom_templates.create_template(db, form, logo_bytes, signature_bytes, images)
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except ImageRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _custom_detail(template, request)


@router.get("/{template_id}", response_model=TemplateDetail, summary="Get one template", responses=NOT_FOUND_TEMPLATE)
def get_template(template_id: str, request: Request, db: DbSession) -> TemplateDetail:
    """A built-in design, or a custom template with all its branding settings."""
    if template_id in TEMPLATES:
        return TemplateDetail(**_builtin_out(TEMPLATES[template_id], request).model_dump())
    return _custom_detail(_custom_or_404(db, template_id), request)


@router.get(
    "/{template_id}/preview",
    response_class=Response,
    summary="Preview a template",
    responses={**PDF_RESPONSE, **NOT_FOUND_TEMPLATE, 409: error("A custom template's image file is missing.")},
)
def preview_template(template_id: str, db: DbSession, settings: AppSettings) -> Response:
    """A sample certificate with placeholder data ("Jane Doe") and a sample QR code, shown in the browser."""
    if template_id in TEMPLATES:
        template: "str | custom_templates.CustomTemplateSpec" = template_id
    else:
        template = custom_templates.to_spec(_custom_or_404(db, template_id), settings.upload_dir)
    try:
        pdf = render_preview_pdf(template, settings.public_base_url)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="template_{template_id}_preview.pdf"'},
    )


@router.delete(
    "/{template_id}",
    status_code=204,
    summary="Delete a custom template",
    responses={**NOT_FOUND_TEMPLATE, 409: error("Built-in template, or a job uses this template.")},
)
def delete_template(template_id: str, db: DbSession, images: Annotated[ImageStore, Depends(get_images)]) -> Response:
    """Deletes the template and its images. Refused (409) while any job uses it."""
    try:
        custom_templates.delete_template(db, template_id, images)
    except custom_templates.TemplateNotFoundError:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    except (custom_templates.BuiltinTemplateError, custom_templates.TemplateInUseError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=204)


# ---------- Helpers ----------


def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read at most max_bytes + 1, so an oversized upload is detected without reading all of it."""
    content = upload.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{upload.filename or 'file'}: larger than {max_bytes // 1024} KB")
    return content


def _custom_or_404(db: Session, template_id: str) -> CustomTemplate:
    try:
        return custom_templates.get_template(db, template_id)
    except custom_templates.TemplateNotFoundError:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")


def _builtin_out(t: Template, request: Request) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        type="builtin",
        name=t.name,
        description=t.description,
        preview_url=str(request.url_for("preview_template", template_id=t.id)),
    )


def _custom_out(t: CustomTemplate, request: Request) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        type="custom",
        name=t.name,
        description=f"Custom template ({t.layout}, {t.font}, {t.border_style} border).",
        preview_url=str(request.url_for("preview_template", template_id=t.id)),
    )


def _custom_detail(t: CustomTemplate, request: Request) -> TemplateDetail:
    return TemplateDetail(
        **_custom_out(t, request).model_dump(),
        primary_color=t.primary_color,
        secondary_color=t.secondary_color,
        font=t.font,
        title_text=t.title_text,
        body_text=t.body_text,
        border_style=t.border_style,
        layout=t.layout,
        has_logo=True,
        has_signature=t.signature_file is not None,
        signer_name=t.signer_name,
        signer_title=t.signer_title,
        owner_id=t.owner_id,
        created_at=t.created_at,
    )
