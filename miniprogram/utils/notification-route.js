const MANAGER_ROLES = ["SUPERVISOR", "PROPERTY_ADMIN", "TENANT_ADMIN"];

function positiveInteger(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
}

function scopeMatches(grant, tenantId, propertyId) {
  if (!grant) {
    return false;
  }
  if (grant.scope_type === "SYSTEM") {
    return true;
  }
  if (Number(grant.tenant_id) !== tenantId) {
    return false;
  }
  if (grant.scope_type === "TENANT") {
    return true;
  }
  return (
    grant.scope_type === "PROPERTY" && Number(grant.property_id) === propertyId
  );
}

function grantAllows(me, permission, tenantId, propertyId, allowedRoles) {
  return ((me && me.scopes) || []).some((grant) => {
    const permissions = grant.permissions || [];
    const roleAllowed =
      !allowedRoles || allowedRoles.indexOf(String(grant.role_code || "")) >= 0;
    return (
      roleAllowed &&
      permissions.indexOf(permission) >= 0 &&
      scopeMatches(grant, tenantId, propertyId)
    );
  });
}

function resourceIdentity(notification) {
  if (!notification) {
    return null;
  }
  const resourceId = positiveInteger(notification.resource_id);
  const tenantId = positiveInteger(notification.tenant_id);
  const propertyId = positiveInteger(notification.property_id);
  if (!resourceId || !tenantId || !propertyId) {
    return null;
  }
  return {
    resourceId,
    tenantId,
    propertyId,
    type: String(notification.resource_type || ""),
  };
}

function staticTarget(notification, me) {
  const resource = resourceIdentity(notification);
  if (!resource) {
    return null;
  }
  if (
    resource.type === "ANNOUNCEMENT" &&
    grantAllows(me, "announcement:read", resource.tenantId, resource.propertyId)
  ) {
    return `/pages/announcements/detail?id=${resource.resourceId}&propertyId=${resource.propertyId}`;
  }
  if (
    resource.type === "RESIDENT_BIND_REQUEST" &&
    grantAllows(me, "resident_binding:read", resource.tenantId, resource.propertyId)
  ) {
    return `/pages/houses/houses?requestId=${resource.resourceId}`;
  }
  return null;
}

function managerTarget(notification, me) {
  const resource = resourceIdentity(notification);
  if (!resource || resource.type !== "WORK_ORDER") {
    return null;
  }
  const canManage =
    grantAllows(
      me,
      "work_order:dispatch",
      resource.tenantId,
      resource.propertyId,
      MANAGER_ROLES
    ) ||
    grantAllows(
      me,
      "work_order:decide",
      resource.tenantId,
      resource.propertyId,
      MANAGER_ROLES
    );
  if (!canManage) {
    return null;
  }
  return `/pages/supervisor/queue?propertyId=${resource.propertyId}&tenantId=${resource.tenantId}&workOrderId=${resource.resourceId}`;
}

module.exports = {
  MANAGER_ROLES,
  positiveInteger,
  resourceIdentity,
  scopeMatches,
  grantAllows,
  staticTarget,
  managerTarget,
};
