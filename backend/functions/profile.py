

import datetime
import secrets
from typing import List
from fastapi import HTTPException, status
from postgrest import APIResponse
from supabase import Client

from schema.auth import CurrentUser, BundleType
from schema.profile import MyProfile, EditProfile, EmployeeDirectory, CreateUserRequest, CreateUserResponse
from supabase_auth import AdminUserAttributes


def get_my_profile(supabase: Client, currentUser:CurrentUser) -> MyProfile:
    response = (
        supabase.table("users")
        .select(
            """
            first_name,
            last_name,
            email,
            personal_email,
            location,
            designation,
            bio,
            skills,
            certification,
            dob,
            nationality,
            gender,
            marital_status,
            doj,
            uan_no,
            pan_no,
            ifsc_code,
            bank_name,
            acc_number,
            companies ( name ),
            departments ( name ),
            manager:manager_id ( first_name, last_name )
        """
        )
        .eq("id", currentUser.id)
        .single()
        .execute()
    )

    data = response.data
    if not data:
        raise HTTPException(status_code=422 , detail="User Not Found!") 

    comp_data = data.get("companies")
    comp_name = comp_data.get("name") if isinstance(comp_data, dict) else ""

    dept_data = data.get("departments")
    dept_name = dept_data.get("name") if isinstance(dept_data, dict) else None

    manager_data = data.get("manager")
    manager_name = None
    if isinstance(manager_data, dict) and manager_data.get("first_name"):
        manager_name = f"{manager_data.get('first_name', '')} {manager_data.get('last_name', '')}".strip()

    return MyProfile(
        f_name=data.get("first_name"),
        l_name=data.get("last_name"),
        designation=data.get("designation"),
        email=data.get("email"),
        comp_name=comp_name,
        dept_name=dept_name,
        location=data.get("location"),
        bio=data.get("bio"),
        p_email=data.get("personal_email"),
        dob=data.get("dob"),
        nationality=data.get("nationality"),
        manager_name=manager_name,
        skills=data.get("skills") or [],
        certification=data.get("certification") or [],
        gender=data.get("gender"),
        marital_status=data.get("marital_status"),
        doj=data.get("doj"),
        uan_no=data.get("uan_no"),
        pan_no=data.get("pan_no"),
        ifsc_code=data.get("ifsc_code"),
        bank_name=data.get("bank_name"),
        acc_number=data.get("acc_number"),
    )


def edit_my_profile(supabase: Client, currentUser: CurrentUser, profile_data: EditProfile) -> MyProfile:
    raw_dict = profile_data.model_dump(exclude_unset=True)
    if raw_dict:
        update_payload = {}
        for key, value in raw_dict.items():
            db_key = "personal_email" if key == "p_email" else key
            if isinstance(value, (datetime.datetime, datetime.date)):
                update_payload[db_key] = value.isoformat()
            else:
                update_payload[db_key] = value

        supabase.table("users").update(update_payload).eq("id", currentUser.id).execute()

    return get_my_profile(supabase, currentUser)


def get_company_directory(supabase: Client, currentUser: CurrentUser) -> List[EmployeeDirectory]:
    user_res = (
        supabase.table("users")
        .select("company_id")
        .eq("id", currentUser.id)
        .single()
        .execute()
    )
    user_data = user_res.data or {}
    company_id = user_data.get("company_id")

    query = supabase.table("users").select(
        """
        id,
        first_name,
        last_name,
        email,
        phone,
        role,
        bio,
        location,
        company_id,
        departments ( name )
    """
    )

    if company_id is not None:
        query = query.eq("company_id", company_id)

    res = query.execute()

    directory: List[EmployeeDirectory] = []
    for emp in res.data or []:
        f_name = emp.get("first_name") or ""
        l_name = emp.get("last_name") or ""
        full_name = f"{f_name} {l_name}".strip() or ""

        dept_data = emp.get("departments")
        dept_name = dept_data.get("name") if isinstance(dept_data, dict) else ""

        designation = emp.get("designation") or ""

        directory.append(
            EmployeeDirectory(
                id=str(emp.get("id")) if emp.get("id") else None,
                name=full_name,
                f_name=f_name,
                l_name=l_name,
                designation=designation,
                dept=dept_name,
                email=emp.get("email") or "",
                phone=emp.get("phone"),
                bio=emp.get("bio"),
                location=emp.get("location"),
                
            )
        )

    return directory


def create_user(
    supabase: Client,
    currentUser: CurrentUser,
    user_data: CreateUserRequest,
    supabase_admin: Client = None,
) -> CreateUserResponse:
    import re
    import string

    role_val = currentUser.role

    if role_val != BundleType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can create new users",
        )

    admin_client = supabase_admin if supabase_admin is not None else supabase

    # Get admin's company_id from users table
    admin_res = (
        supabase.table("users")
        .select("company_id")
        .eq("id", currentUser.id)
        .single()
        .execute()
    )
    admin_data = admin_res.data or {}
    company_id = admin_data.get("company_id")

    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin user is not associated with any company",
        )

    # Determine Year of Joining
    if user_data.doj:
        year_val = user_data.doj.year
        doj_iso = user_data.doj.isoformat()
    else:
        now = datetime.datetime.now()
        year_val = now.year
        doj_iso = now.isoformat()
    year_str = str(year_val)

    # Query existing user IDs for this company to find the current year's highest serial number
    existing_users_res = (
        admin_client.table("users")
        .select("id")
        .eq("company_id", company_id)
        .execute()
    )
    existing_ids = [u.get("id", "") for u in (existing_users_res.data or []) if u.get("id")]

    max_serial = 0
    pattern = re.compile(rf"^[A-Za-z]{{2}}{year_str}(\d{{3,4}})$")
    for uid_str in existing_ids:
        m = pattern.match(uid_str)
        if m:
            try:
                num = int(m.group(1))
                if num > max_serial:
                    max_serial = num
            except ValueError:
                pass

    serial_num = max_serial + 1
    padded_serial = f"{serial_num:03d}"

    # Generate employee_id: First 2 letters of first_name + year + 3-digit serial number
    fn_clean = user_data.first_name.strip()
    fn_code = (fn_clean[:2] if len(fn_clean) >= 2 else (fn_clean + "X")[:2]).upper()
    employee_id = f"{fn_code}{year_str}{padded_serial}"

    # Generate secure temporary password (e.g. 10 chars with letters, numbers, and special symbols)
    chars = string.ascii_letters + string.digits + "!@#$%*"
    random_password = "".join(secrets.choice(chars) for _ in range(10))

    # Create auth user in Supabase Auth with temporary password & must_change_password flag
    auth_user_uid = None
    try:
        auth_res = admin_client.auth.admin.create_user(
            AdminUserAttributes(
                email=user_data.email.strip(),
                password=random_password,
                email_confirm=True,
                user_metadata={
                    "first_name": user_data.first_name.strip(),
                    "last_name": user_data.last_name.strip(),
                    "login_id": employee_id,
                    "must_change_password": True,
                },
            )
        )
        if auth_res and hasattr(auth_res, "user") and auth_res.user:
            auth_user_uid = auth_res.user.id
    except Exception as e:
        print(f"Error creating Supabase auth user with admin client: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create auth user: {str(e)}",
        )

    # Convert dept_id to int if integer-like
    dept_id_val = user_data.department_id
    if isinstance(dept_id_val, str) and dept_id_val.isdigit():
        dept_id_val = int(dept_id_val)

    # Insert into users table using admin_client
    user_payload = {
        "id": employee_id,
        "first_name": user_data.first_name.strip(),
        "last_name": user_data.last_name.strip(),
        "email": user_data.email.strip(),
        "phone": user_data.phone.strip() if user_data.phone else None,
        "dept_id": dept_id_val,
        "designation": user_data.designation.strip() if user_data.designation else None,
        "doj": doj_iso,
        "company_id": company_id,
        "role": "employee",
        "uid": str(auth_user_uid) if auth_user_uid else None,
    }

    insert_res = admin_client.table("users").insert(user_payload).execute()
    inserted_records = insert_res.data or []
    inserted_user = inserted_records[0] if inserted_records else {}

    return CreateUserResponse(
        id=str(inserted_user.get("id") or employee_id),
        employee_id=employee_id,
        uid=str(auth_user_uid) if auth_user_uid else None,
        first_name=user_data.first_name.strip(),
        last_name=user_data.last_name.strip(),
        email=user_data.email.strip(),
        password=random_password,
        temporary_password=random_password,
        phone=user_data.phone,
        department_id=user_data.department_id,
        designation=user_data.designation,
        doj=user_data.doj,
        company_id=company_id,
        role="employee",
        must_change_password=True,
        message=f"Employee account successfully created. Generated Login ID: {employee_id}",
    )





