const api = require("../../services/api");
const { withLoading } = require("../../utils/ui");
const labels = require("../../utils/business-labels");
const { utcLabel } = require("../../utils/work-order-display");
const EMPLOYEE_STATUS_OPTIONS = ["ACTIVE", "DISABLED"].map((value) => ({
  value,
  label: labels.employeeStatus(value),
}));

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

function loadAllEmployees(propertyId, tenantId, afterId, collected) {
  const current = collected || [];
  return api
    .employees(propertyId, tenantId, afterId || 0, true)
    .then((response) => {
      const items = (response && response.items) || [];
      const next = current.concat(items);
      if (!(response && response.next_cursor)) {
        return next;
      }
      return loadAllEmployees(propertyId, tenantId, response.next_cursor, next);
    });
}

function loadAllDaily(propertyId, tenantId, afterId, collected) {
  const current = collected || [];
  return api.staffDaily(propertyId, tenantId, afterId || 0).then((response) => {
    const items = (response && response.items) || [];
    const next = current.concat(items);
    if (!(response && response.next_cursor)) {
      return next;
    }
    return loadAllDaily(propertyId, tenantId, response.next_cursor, next);
  });
}

function assignmentIsCurrent(assignment) {
  if (!assignment || assignment.status !== "ACTIVE") {
    return false;
  }
  const now = Date.now();
  const from = assignment.valid_from ? Date.parse(assignment.valid_from) : NaN;
  const to = assignment.valid_to ? Date.parse(assignment.valid_to) : NaN;
  return (!Number.isFinite(from) || from <= now) && (!Number.isFinite(to) || to > now);
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
    canInvite: false,
    canReadHistory: false,
    workHistory: [],
    historyLoading: false,
    historyLoaded: false,
    historyError: "",
    historyCursor: null,
    actionBusy: false,
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
    statusOptions: EMPLOYEE_STATUS_OPTIONS,
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
    this._hidden = false;
    return this.bootstrap();
  },

  clearScope(keepProperties = false) {
    this.clearHistory();
    this._viewGeneration = (this._viewGeneration || 0) + 1;
    this._loadGeneration = (this._loadGeneration || 0) + 1;
    this._selectionGeneration = (this._selectionGeneration || 0) + 1;
    this._mutation = null; this._inviteContext = null;
    this.setData({ properties: keepProperties ? this.data.properties : [], propertyId: "", tenantId: "",
      items: [], selected: null, selectedEmployeeId: "", departments: [], duties: [], skills: [],
      departmentOptions: [{ id: "", name: "不设置部门" }], dutyChoices: [], skillChoices: [],
      canEdit: false, canInvite: false, canReadHistory: false, actionBusy: false, loading: false, error: "", notice: "",
      invitePhone: "", inviteToken: "", assignmentProperties: [], assignmentPropertyIndex: 0,
      employeeNo: "", createCapacity: "1", createDepartmentIndex: 0, createDutyIds: [], createSkillIds: [],
      editCapacity: "1", editDepartmentIndex: 0, editStatusIndex: 0, addDutyIds: [], addSkillIds: [],
      dutyCode: "", dutyName: "", skillCode: "", skillName: "" });
    return this._viewGeneration;
  },
  onHide() { this._hidden = true; this.clearScope(); },
  onUnload() { this._hidden = true; this.clearScope(); },

  clearHistory() {
    this._historyGeneration = (this._historyGeneration || 0) + 1;
    this.setData({ workHistory: [], historyLoading: false, historyLoaded: false, historyError: "", historyCursor: null });
  },

  loadMoreHistory() { return this.loadWorkHistory(true); },

  loadWorkHistory(more) {
    if (this._hidden || !this.data.canReadHistory || !this.data.selected || this.data.historyLoading) return Promise.resolve();
    const append = more === true;
    if (append && !this.data.historyCursor) return Promise.resolve();
    if (!append) this.clearHistory();
    const generation = this._historyGeneration, view = this._viewGeneration, selection = this._selectionGeneration;
    const employeeId = Number(this.data.selectedEmployeeId), propertyId = Number(this.data.propertyId), tenantId = Number(this.data.tenantId);
    const current = () => !this._hidden && generation === this._historyGeneration && view === this._viewGeneration && selection === this._selectionGeneration;
    this.setData({ historyLoading: true, historyError: "" });
    return api.employeeWorkHistory(employeeId, propertyId, tenantId, append ? this.data.historyCursor : 0).then(result => {
      if (!current()) return;
      if (!result || result.employee_id !== employeeId || result.property_id !== propertyId || result.tenant_id !== tenantId) throw new Error("履历范围已变化，请刷新后重试");
      const items = (result.items || []).map(item => {
        const mode = { SELF: "提交者自报独立处理", ASSISTED: "提交者自报参与协作", EXTERNAL: "自报由支援人员处理" };
        const feedback = item.latest_feedback;
        const feedbackText = !feedback ? "未记录住户反馈" : [
          feedback.confirmed === true ? "本条明确确认" : feedback.unresolved ? "本条反馈未解决" : "本条未明确确认结果",
          feedback.rating == null ? "未评分" : "评分 " + feedback.rating + "/5"
        ].join("；");
        return { ...item, statusText: labels.workOrderStatus(item.current_order_status), startedAtLabel: utcLabel(item.started_at),
          categoryText: ({PLUMBING:"水管维修",ELECTRICAL:"电气维修",HVAC:"空调维修",FACILITY:"设施维修",CLEANING:"保洁",SECURITY:"安保",OTHER:"其他"})[String(item.category).toUpperCase()] || "其他",
          handlingText: (mode[item.handling_mode] || "未记录实际参与情况") +
            (item.reported_participant_employee_id && item.reported_participant_employee_id !== employeeId ? "（参与人为其他员工）" : ""),
          receiptText: item.reported_resolved == null ? "尚无回执" : item.reported_resolved ? "员工回执称已解决" : "员工回执称未解决",
          feedbackText };
      });
      this.setData({ workHistory: append ? this.data.workHistory.concat(items) : items,
        historyCursor: result.next_cursor, historyLoaded: true });
    }).catch(error => {
      if (!current()) return;
      this.clearHistory();
      this.setData({ historyError: error.message || "履历读取失败" });
      if (error.statusCode === 403 || error.statusCode === 401) return this.bootstrap();
    }).finally(() => { if (current()) this.setData({ historyLoading: false }); });
  },

  _mutate(work, selection = false) {
    if (this._hidden || !this.data.canEdit || this._mutation) return Promise.resolve();
    const view = this._viewGeneration, load = this._loadGeneration, selected = this._selectionGeneration;
    const current = () => !this._hidden && view === this._viewGeneration && load === this._loadGeneration &&
      (!selection || selected === this._selectionGeneration);
    const token = {}; this._mutation = token;
    this.setData({ actionBusy: true });
    const page = { setData: values => { if (current()) this.setData(values); } };
    return withLoading(page, () => current() ? work(current) : undefined).catch(error => {
      if (current() && (error.statusCode === 403 || error.statusCode === 401)) {
        this.requestedPropertyId = this.data.propertyId;
        return this.bootstrap();
      }
    }).finally(() => {
      if (this._mutation === token) {
        this._mutation = null;
        if (!this._hidden && view === this._viewGeneration) this.setData({ actionBusy: false,
          ...(load === this._loadGeneration ? { loading: false } : {}) });
      }
    });
  },

  bootstrap() {
    if (this._hidden) return Promise.resolve();
    const generation = this.clearScope();
    this.setData({ loading: true });
    return Promise.all([api.me(), loadAllProperties()]).then(([me, allProperties]) => {
        if (this._hidden || generation !== this._viewGeneration) return;
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
            canInvite: scopes.some(scope => (scope.permissions || []).includes("employee:manage") &&
              (scope.scope_type === "SYSTEM" || (scope.scope_type === "TENANT" && Number(scope.tenant_id) === Number(item.tenant_id)))),
            canReadHistory: scopes.some(scope => scopeAllows(scope, "work_order:dispatch", item)),
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
          canInvite: current.canInvite,
          canReadHistory: current.canReadHistory,
        });
        return this.loadData();
      }).catch(error => {
        if (this._hidden || generation !== this._viewGeneration) return;
        this.clearScope(); this.setData({ error: error.message || "身份范围读取失败" });
      });
  },

  onPropertyChange(event) {
    const propertyIndex = Number(event.detail.value);
    const current = this.data.properties[propertyIndex];
    if (!current) {
      return;
    }
    this.clearScope(true);
    this.requestedPropertyId = String(current.id);
    this.setData({
      propertyIndex,
      propertyId: String(current.id),
      tenantId: String(current.tenant_id),
      canEdit: current.canEdit,
      canInvite: current.canInvite,
      canReadHistory: current.canReadHistory,
      selected: null,
      selectedEmployeeId: "",
      inviteToken: "",
      notice: "",
    });
    return this.loadData();
  },

  load() {
    this.requestedPropertyId = this.data.propertyId || this.requestedPropertyId;
    return this.bootstrap();
  },

  loadData() {
    if (this._hidden) return Promise.resolve();
    this.clearHistory();
    const view = this._viewGeneration;
    const generation = this._loadGeneration = (this._loadGeneration || 0) + 1;
    const selectedId = this.data.selectedEmployeeId;
    const current = () => !this._hidden && view === this._viewGeneration && generation === this._loadGeneration;
    const propertyId = this.data.propertyId;
    const tenantId = this.data.tenantId;
    if (!propertyId || !tenantId) {
      this.setData({ error: "缺少小区范围" }); return Promise.resolve();
    }
    this._selectionGeneration = (this._selectionGeneration || 0) + 1;
    this._inviteContext = null;
    this.setData({ items: [], selected: null, invitePhone: "", inviteToken: "", loading: true, error: "" });
    return Promise.all([
      api.employeeCatalog(propertyId, tenantId),
      loadAllEmployees(propertyId, tenantId),
      loadAllDaily(propertyId, tenantId),
    ]).then(([catalog, employeeItems, dailyItems]) => {
      if (!current()) return;
      const departments = (catalog && catalog.departments) || [];
      const duties = (catalog && catalog.duties) || [];
      const skills = (catalog && catalog.skills) || [];
      const dailyByEmployee = {};
      dailyItems.forEach((item) => {
        dailyByEmployee[item.employee_id] = item;
      });
      const items = employeeItems.map(
        (detail) => {
          const employee = detail.employee;
          const department = departments.find(
            (item) => Number(item.id) === Number(employee.department_id)
          );
          const daily = dailyByEmployee[employee.id] || null;
          const hasCurrentAssignment = (detail.assignments || []).some(
            (assignment) =>
              Number(assignment.property_id) === Number(propertyId) &&
              assignmentIsCurrent(assignment)
          );
          return {
            ...detail,
            employee: {
              ...employee,
              statusText: labels.employeeStatus(employee.status),
            },
            assignments: (detail.assignments || []).map((assignment) => ({
              ...assignment,
              statusText: labels.relationStatus(assignment.status),
              propertyName:
                (this.data.properties.find(
                  (property) => Number(property.id) === Number(assignment.property_id)
                ) || {}).name || "已授权小区",
            })),
            id: employee.id,
            departmentName: department ? department.name : "未设置",
            dutyNames: (detail.duties || []).map((item) => item.name).join("、") || "无",
            skillNames: (detail.skills || []).map((item) => item.name).join("、") || "无",
            daily,
            hasCurrentAssignment,
            assignmentText: hasCurrentAssignment ? "当前有效" : "历史任职，可重新建立",
            dutyText: daily ? `${labels.onDutyStatus(daily.on_duty)} / 当前任务 ${daily.current_load}` : "今日未签到",
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
      if (selectedId) {
        this.selectEmployeeById(selectedId, items, departmentOptions);
      }
    }).catch(error => { if (current()) this.setData({ error: error.message || "人员读取失败", canEdit: false, canInvite: false, canReadHistory: false }); })
      .finally(() => { if (current()) this.setData({ loading: false }); });
  },

  selectEmployee(event) {
    this.selectEmployeeById(event.currentTarget.dataset.id);
  },

  selectEmployeeById(employeeId, suppliedItems, suppliedDepartments) {
    if (this._hidden) return;
    this.clearHistory();
    this._selectionGeneration = (this._selectionGeneration || 0) + 1;
    this._inviteContext = null;
    this.setData({ invitePhone: "", inviteToken: "" });
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
    let editStatusIndex = this.data.statusOptions.findIndex(
      (item) => item.value === detail.employee.status
    );
    if (editStatusIndex < 0) {
      editStatusIndex = 0;
    }
    const assignmentProperties = this.data.properties.filter(
      (property) =>
        Number(property.tenant_id) === Number(detail.employee.tenant_id) &&
        !(detail.assignments || []).some(
          (assignment) =>
            assignmentIsCurrent(assignment) &&
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
    if (event.currentTarget.dataset.field === "invitePhone") {
      this._inviteContext = null; this.setData({ inviteToken: "" });
    }
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
    const body = {
          tenant_id: Number(this.data.tenantId),
          property_id: Number(this.data.propertyId),
          employee_no: employeeNo,
          department_id: department && department.id ? Number(department.id) : undefined,
          duty_ids: this.data.createDutyIds.slice(),
          skill_ids: this.data.createSkillIds.slice(),
          max_capacity: maxCapacity,
        };
    return this._mutate(current => api.createEmployee(body)
        .then((employee) => {
          if (!current()) return;
          this.setData({
            employeeNo: "",
            createCapacity: "1",
            createDepartmentIndex: 0,
            createDutyIds: [],
            createSkillIds: [],
            selectedEmployeeId: String(employee.id),
            notice: this.data.canInvite ? "员工已创建，可继续发出认领邀请" : "员工已创建，请由企业管理员发出认领邀请",
          });
          return this.loadData();
        })
    );
  },

  updateEmployee() {
    if (!this.data.selected) {
      return;
    }
    const maxCapacity = Number(this.data.editCapacity);
    const employeeId = this.data.selected.employee.id;
    if (!Number.isInteger(maxCapacity) || maxCapacity < 1) {
      this.setData({ error: "容量必须是正整数" });
      return;
    }
    const department = this.data.departmentOptions[this.data.editDepartmentIndex];
    const body = {
      status: this.data.statusOptions[this.data.editStatusIndex].value,
      max_capacity: maxCapacity,
      add_duty_ids: this.data.addDutyIds.slice(),
      add_skill_ids: this.data.addSkillIds.slice(),
      clear_department: !(department && department.id),
    };
    if (department && department.id) {
      body.department_id = Number(department.id);
    }
    return this._mutate(current =>
      api.updateEmployee(employeeId, body).then(() => {
        if (!current()) return;
        this.setData({ notice: "员工资料已更新" });
        return this.loadData();
      })
    , true);
  },

  issueInvite() {
    if (!this.data.canInvite || this._hidden) {
      this.setData({ error: "请由具备企业范围权限的管理员发出邀请" }); return;
    }
    if (this._mutation) return;
    if (!this.data.selected || !this.data.invitePhone.trim()) {
      this.setData({ error: "请先选择员工并填写手机号" });
      return;
    }
    if (this.data.selected.employee.user_id || this.data.selected.employee.status !== "ACTIVE") {
      this.setData({ error: "仅可邀请有效且尚未认领的员工" }); return;
    }
    const employeeId = this.data.selected.employee.id, phone = this.data.invitePhone.trim();
    const context = { view: this._viewGeneration, propertyId: this.data.propertyId, employeeId: String(employeeId), phone };
    this._inviteContext = null; this.setData({ inviteToken: "" });
    return this._mutate(current =>
      api.issueEmployeeInvite(employeeId, phone)
        .then((result) => {
          if (!current() || this.data.invitePhone.trim() !== phone) return;
          this._inviteContext = context;
          this.setData({
            inviteToken: result.invite_token || "",
            notice: "邀请已生成，请由员工本人登录后认领",
          });
        })
    , true);
  },

  copyInviteToken() {
    const context = this._inviteContext;
    if (!this._hidden && this.data.canInvite && this.data.inviteToken && context &&
        context.view === this._viewGeneration && context.propertyId === this.data.propertyId &&
        context.employeeId === this.data.selectedEmployeeId && context.phone === this.data.invitePhone.trim()) {
      wx.setClipboardData({ data: this.data.inviteToken });
    }
  },

  assignProperty() {
    const property = this.data.assignmentProperties[this.data.assignmentPropertyIndex];
    if (!this.data.selected || !property) {
      this.setData({ error: "没有可新增的服务小区" });
      return;
    }
    const employeeId = this.data.selected.employee.id, propertyId = property.id;
    return this._mutate(current =>
      api.assignEmployeeProperty(employeeId, propertyId).then(() => {
        if (!current()) return;
        this.setData({ notice: "服务小区任职已生效" });
        return this.loadData();
      })
    , true);
  },

  endAssignment(event) {
    if (!this.data.selected) {
      return;
    }
    const assignmentId = event.currentTarget.dataset.id;
    const employeeId = this.data.selected.employee.id;
    return this._mutate(current =>
      api
        .endEmployeeProperty(employeeId, assignmentId)
        .then(() => {
          if (!current()) return;
          this.setData({ notice: "任职已结束并保留历史" });
          return this.loadData();
        })
    , true);
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
    return this._mutate(current =>
      (isDuty ? api.createDuty(body) : api.createSkill(body)).then(() => {
        if (!current()) return;
        this.setData(
          isDuty
            ? { dutyCode: "", dutyName: "", notice: "职责已创建" }
            : { skillCode: "", skillName: "", notice: "技能已创建" }
        );
        return this.loadData();
      })
    );
  },

  createDuty() {
    this.createCatalogItem("duty");
  },

  createSkill() {
    this.createCatalogItem("skill");
  },
});
