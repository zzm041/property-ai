const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");

function numericIds(values) {
  return (values || []).map((value) => Number(value)).filter((value) => value > 0);
}

function hasId(items, id) {
  return (items || []).some((item) => Number(item.id) === Number(id));
}

function scopeAllows(scope, permission, property) {
  if ((scope.permissions || []).indexOf(permission) < 0) {
    return false;
  }
  if (scope.scope_type === "SYSTEM") {
    return true;
  }
  if (Number(scope.tenant_id) !== Number(property.tenant_id)) {
    return false;
  }
  return (
    scope.scope_type === "TENANT" ||
    (scope.scope_type === "PROPERTY" &&
      Number(scope.property_id) === Number(property.id))
  );
}

function loadAllProperties(afterId, collected) {
  const current = collected || [];
  return api.catalogProperties(afterId || 0).then((response) => {
    const items = (response && response.items) || [];
    const next = current.concat(items);
    if (items.length < 100) {
      return next;
    }
    return loadAllProperties(items[items.length - 1].id, next);
  });
}

Page({
  data: {
    properties: [],
    propertyIndex: 0,
    propertyId: "",
    tenantId: "",
    departments: [],
    departmentOptions: [{ id: "", name: "不设置部门" }],
    duties: [],
    skills: [],
    dutyChoices: [],
    skillChoices: [],
    items: [],
    selected: null,
    selectedEmployeeId: "",
    canEdit: false,
    loading: false,
    error: "",
    notice: "",
    employeeNo: "",
    createCapacity: "1",
    createDepartmentIndex: 0,
    createDutyIds: [],
    createSkillIds: [],
    editCapacity: "1",
    editDepartmentIndex: 0,
    editStatusIndex: 0,
    statusOptions: ["ACTIVE", "DISABLED"],
    addDutyIds: [],
    addSkillIds: [],
    invitePhone: "",
    inviteToken: "",
    assignmentPropertyIndex: 0,
    assignmentProperties: [],
    dutyCode: "",
    dutyName: "",
    skillCode: "",
    skillName: "",
  },

  onLoad(query) {
    this.requestedPropertyId = query.propertyId || "";
    this.requestedTenantId = query.tenantId || "";
  },

  onShow() {
    this.bootstrap();
  },

  bootstrap() {
    withLoading(this, () =>
      Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        const scopes = me.scopes || [];
        const properties = allProperties
          .filter((item) =>
            scopes.some((scope) => scopeAllows(scope, "employee:read", item))
          )
          .map((item) => ({
            ...item,
            canEdit: scopes.some((scope) =>
              scopeAllows(scope, "employee:manage", item)
            ),
          }));
        if (!properties.length) {
          throw new Error("当前身份没有可管理员工的小区范围");
        }
        let propertyIndex = properties.findIndex(
          (item) => Number(item.id) === Number(this.requestedPropertyId)
        );
        if (propertyIndex < 0) {
          propertyIndex = 0;
        }
        const current = properties[propertyIndex];
        this.setData({
          properties,
          propertyIndex,
          propertyId: String(current.id),
          tenantId: String(current.tenant_id),
          canEdit: current.canEdit,
        });
        return this.loadData();
      })
    ).catch(() => {});
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const current = this.data.properties[propertyIndex];
    if (!current) {
      return;
    }
    this.setData({
      propertyIndex,
      propertyId: String(current.id),
      tenantId: String(current.tenant_id),
      canEdit: current.canEdit,
      selected: null,
      selectedEmployeeId: "",
      inviteToken: "",
      notice: "",
    });
    this.load();
  },

  load() {
    withLoading(this, () => this.loadData()).catch(() => {});
  },

  loadData() {
    const propertyId = this.data.propertyId;
    const tenantId = this.data.tenantId;
    if (!propertyId || !tenantId) {
      return Promise.reject(new Error("缺少小区范围"));
    }
    return Promise.all([
      api.employeeCatalog(propertyId, tenantId),
      api.employees(propertyId, tenantId),
      api.staffDaily(propertyId, tenantId),
    ]).then(([catalog, employeeResponse, dailyResponse]) => {
      const departments = (catalog && catalog.departments) || [];
      const duties = (catalog && catalog.duties) || [];
      const skills = (catalog && catalog.skills) || [];
      const dailyByEmployee = {};
      ((dailyResponse && dailyResponse.items) || []).forEach((item) => {
        dailyByEmployee[item.employee_id] = item;
      });
      const items = ((employeeResponse && employeeResponse.items) || []).map(
        (detail) => {
          const employee = detail.employee;
          const department = departments.find(
            (item) => Number(item.id) === Number(employee.department_id)
          );
          const daily = dailyByEmployee[employee.id] || null;
          return {
            ...detail,
            id: employee.id,
            departmentName: department ? department.name : "未设置",
            dutyNames: (detail.duties || []).map((item) => item.name).join("、") || "无",
            skillNames: (detail.skills || []).map((item) => item.name).join("、") || "无",
            daily,
            dutyText: daily ? `${daily.on_duty} / 负载 ${daily.current_load}` : "今日未签到",
          };
        }
      );
      const departmentOptions = [{ id: "", name: "不设置部门" }].concat(departments);
      this.setData({
        departments,
        departmentOptions,
        duties,
        skills,
        items,
        error: "",
      });
      if (this.data.selectedEmployeeId) {
        this.selectEmployeeById(this.data.selectedEmployeeId, items, departmentOptions);
      }
    });
  },

  selectEmployee(event) {
    this.selectEmployeeById(event.currentTarget.dataset.id);
  },

  selectEmployeeById(employeeId, suppliedItems, suppliedDepartments) {
    const items = suppliedItems || this.data.items;
    const detail = items.find(
      (item) => Number(item.employee.id) === Number(employeeId)
    );
    if (!detail) {
      this.setData({ selected: null, selectedEmployeeId: "" });
      return;
    }
    const departmentOptions = suppliedDepartments || this.data.departmentOptions;
    let editDepartmentIndex = departmentOptions.findIndex(
      (item) => Number(item.id) === Number(detail.employee.department_id)
    );
    if (editDepartmentIndex < 0) {
      editDepartmentIndex = 0;
    }
    let editStatusIndex = this.data.statusOptions.indexOf(detail.employee.status);
    if (editStatusIndex < 0) {
      editStatusIndex = 0;
    }
    const assignmentProperties = this.data.properties.filter(
      (property) =>
        Number(property.tenant_id) === Number(detail.employee.tenant_id) &&
        !(detail.assignments || []).some(
          (assignment) =>
            assignment.status === "ACTIVE" &&
            Number(assignment.property_id) === Number(property.id)
        )
    );
    this.setData({
      selected: detail,
      selectedEmployeeId: String(detail.employee.id),
      editCapacity: String(detail.employee.max_capacity),
      editDepartmentIndex,
      editStatusIndex,
      addDutyIds: [],
      addSkillIds: [],
      dutyChoices: this.data.duties.map((item) => ({
        ...item,
        assigned: hasId(detail.duties, item.id),
      })),
      skillChoices: this.data.skills.map((item) => ({
        ...item,
        assigned: hasId(detail.skills, item.id),
      })),
      inviteToken: "",
      assignmentProperties,
      assignmentPropertyIndex: 0,
      notice: "",
    });
  },

  onTextInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  onCreateDepartmentChange(event) {
    this.setData({ createDepartmentIndex: Number(event.detail.value) });
  },

  onEditDepartmentChange(event) {
    this.setData({ editDepartmentIndex: Number(event.detail.value) });
  },

  onEditStatusChange(event) {
    this.setData({ editStatusIndex: Number(event.detail.value) });
  },

  onAssignmentPropertyChange(event) {
    this.setData({ assignmentPropertyIndex: Number(event.detail.value) });
  },

  onCreateDuties(event) {
    this.setData({ createDutyIds: numericIds(event.detail.value) });
  },

  onCreateSkills(event) {
    this.setData({ createSkillIds: numericIds(event.detail.value) });
  },

  onAddDuties(event) {
    this.setData({ addDutyIds: numericIds(event.detail.value) });
  },

  onAddSkills(event) {
    this.setData({ addSkillIds: numericIds(event.detail.value) });
  },

  createEmployee() {
    const employeeNo = this.data.employeeNo.trim();
    const maxCapacity = Number(this.data.createCapacity);
    if (!employeeNo || !Number.isInteger(maxCapacity) || maxCapacity < 1) {
      this.setData({ error: "请填写员工编号和有效容量" });
      return;
    }
    const department = this.data.departmentOptions[this.data.createDepartmentIndex];
    withLoading(this, () =>
      api
        .createEmployee({
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          employee_no: employeeNo,
          department_id: department && department.id ? Number(department.id) : undefined,
          duty_ids: this.data.createDutyIds,
          skill_ids: this.data.createSkillIds,
          max_capacity: maxCapacity,
        })
        .then((employee) => {
          this.setData({
            employeeNo: "",
            createCapacity: "1",
            createDepartmentIndex: 0,
            createDutyIds: [],
            createSkillIds: [],
            selectedEmployeeId: String(employee.id),
            notice: "员工已创建，可继续发出认领邀请",
          });
          return this.loadData();
        })
    ).catch(() => {});
  },

  updateEmployee() {
    if (!this.data.selected) {
      return;
    }
    const maxCapacity = Number(this.data.editCapacity);
    if (!Number.isInteger(maxCapacity) || maxCapacity < 1) {
      this.setData({ error: "容量必须是正整数" });
      return;
    }
    const department = this.data.departmentOptions[this.data.editDepartmentIndex];
    const body = {
      status: this.data.statusOptions[this.data.editStatusIndex],
      max_capacity: maxCapacity,
      add_duty_ids: this.data.addDutyIds,
      add_skill_ids: this.data.addSkillIds,
      clear_department: !(department && department.id),
    };
    if (department && department.id) {
      body.department_id = Number(department.id);
    }
    withLoading(this, () =>
      api.updateEmployee(this.data.selected.employee.id, body).then(() => {
        this.setData({ notice: "员工资料已更新" });
        return this.loadData();
      })
    ).catch(() => {});
  },

  issueInvite() {
    if (!this.data.selected || !this.data.invitePhone.trim()) {
      this.setData({ error: "请先选择员工并填写手机号" });
      return;
    }
    withLoading(this, () =>
      api
        .issueEmployeeInvite(
          this.data.selected.employee.id,
          this.data.invitePhone.trim()
        )
        .then((result) => {
          this.setData({
            inviteToken: result.invite_token || "",
            notice: "邀请已生成，请由员工本人登录后认领",
          });
        })
    ).catch(() => {});
  },

  copyInviteToken() {
    if (this.data.inviteToken) {
      wx.setClipboardData({ data: this.data.inviteToken });
    }
  },

  assignProperty() {
    const property = this.data.assignmentProperties[this.data.assignmentPropertyIndex];
    if (!this.data.selected || !property) {
      this.setData({ error: "没有可新增的服务小区" });
      return;
    }
    withLoading(this, () =>
      api.assignEmployeeProperty(this.data.selected.employee.id, property.id).then(() => {
        this.setData({ notice: "服务小区任职已生效" });
        return this.loadData();
      })
    ).catch(() => {});
  },

  endAssignment(event) {
    if (!this.data.selected) {
      return;
    }
    const assignmentId = event.currentTarget.dataset.id;
    withLoading(this, () =>
      api
        .endEmployeeProperty(this.data.selected.employee.id, assignmentId)
        .then(() => {
          this.setData({ notice: "任职已结束并保留历史" });
          return this.loadData();
        })
    ).catch(() => {});
  },

  createCatalogItem(kind) {
    const isDuty = kind === "duty";
    const code = (isDuty ? this.data.dutyCode : this.data.skillCode).trim();
    const name = (isDuty ? this.data.dutyName : this.data.skillName).trim();
    if (!code || !name) {
      this.setData({ error: "请填写编码和名称" });
      return;
    }
    const body = {
      tenant_id: Number(this.data.tenantId),
      property_id: Number(this.data.propertyId),
      code,
      name,
    };
    const operation = isDuty ? api.createDuty(body) : api.createSkill(body);
    withLoading(this, () =>
      operation.then(() => {
        this.setData(
          isDuty
            ? { dutyCode: "", dutyName: "", notice: "职责已创建" }
            : { skillCode: "", skillName: "", notice: "技能已创建" }
        );
        return this.loadData();
      })
    ).catch(() => {});
  },

  createDuty() {
    this.createCatalogItem("duty");
  },

  createSkill() {
    this.createCatalogItem("skill");
  },
});
