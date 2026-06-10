### 1. GROUP MANAGEMENT

---

#### `POST /groups`

**Create a new project group**

- **Auth:** Required
- **Role:** STUDENT (becomes group owner)

```python
# Request body
GroupCreate

# Response 201
GroupOut

# Example
POST /api/v1/groups
{
  "group_name": "Team Alpha",
  "assignment_status": "ACTIVE"
}
```

---

#### `GET /groups`

**List all groups the current user belongs to**

- **Auth:** Required
- **Role:** Any

```python
# Response 200
List[GroupOut]
```

---

#### `GET /groups/{group_id}`

**Get details of a specific group**

- **Auth:** Required
- **Role:** Must be a member of the group

```python
# Path param: group_id (str)

# Response 200
GroupOut

# Response 403 if not a member
{"detail": "You are not a member of this group"}
```

---

#### `PUT /groups/{group_id}`

**Update group name or status**

- **Auth:** Required
- **Role:** Group owner (STUDENT who created it)

```python
# Request body
GroupUpdate

# Response 200
GroupOut
```

---

#### `DELETE /groups/{group_id}`

**Delete a group and all its data**

- **Auth:** Required
- **Role:** Group owner only

```python
# Response 200
{"message": "Group deleted successfully"}

# Note: cascades to assets, transcripts, scores, reports
```

---

### 2. INVITATIONS

---

#### `POST /groups/{group_id}/invite`

**Generate a shareable invite link**

- **Auth:** Required
- **Role:** Group owner or INSTRUCTOR of the group

```python
# Request body
InviteCreate

# Response 201
InviteOut

# Example
POST /api/v1/groups/grp_123/invite
{
  "role": "STUDENT",
  "expires_in_hours": 72
}

# Response
{
  "token": "abc123xyz",
  "invite_url": "https://collabtrack.app/invite/abc123xyz",
  "role": "STUDENT",
  "expires_at": "2026-06-10T12:00:00Z",
  "group_id": "grp_123"
}
```

---

#### `GET /invite/{token}`

**Validate an invite token and return group info**
**Called before login/signup to show the user what they are joining**

- **Auth:** Not required (public endpoint)

```python
# Path param: token (str)

# Response 200
InviteDetails

# Response 404 if token not found
{"detail": "Invitation not found"}

# Response 410 if expired
{"detail": "Invitation has expired"}
```

---

#### `POST /invite/{token}/accept`

**Accept an invitation after the user is logged in**

- **Auth:** Required
- **Role:** Any (role assigned based on invite token role field)

```python
# Path param: token (str)
# No request body needed — uses current user from JWT

# Response 200
{
  "message": "You have successfully joined Team Alpha",
  "group_id": "grp_123",
  "role": "STUDENT"
}

# Response 409 if already a member
{"detail": "You are already a member of this group"}

# Response 410 if expired
{"detail": "Invitation has expired"}
```

---

### 3. MEMBERS

---

#### `GET /groups/{group_id}/members`

**List all members of a group**

- **Auth:** Required
- **Role:** Must be a member of the group

```python
# Response 200
List[MemberOut]
```

---

#### `DELETE /groups/{group_id}/members/{user_id}`

**Remove a member from a group**

- **Auth:** Required
- **Role:** Group owner or INSTRUCTOR

```python
# Path params: group_id, user_id

# Response 200
{"message": "Member removed successfully"}

# Response 403 if trying to remove the owner
{"detail": "Cannot remove the group owner"}
```

---
