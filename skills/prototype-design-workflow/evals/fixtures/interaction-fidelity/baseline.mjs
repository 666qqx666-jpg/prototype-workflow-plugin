export const GROUPS = [
  {id: 'g1', name: '运营'}, {id: 'g2', name: '服务'}, {id: 'g3', name: '协作'},
];
export const MEMBERS = [
  {id: 'm1', name: '甲', groups: ['g2', 'g1'], region: '东区'},
  {id: 'm2', name: '乙', groups: ['g3'], region: '西区'},
];
const copy = value => structuredClone(value);
export function createModel() {
  const records = copy(MEMBERS);
  let filterDraft = '', filterApplied = '', detail = null, groupDraft = null;
  function requireDetail() { if (!detail) throw new Error('DETAIL_CLOSED'); }
  return {
    setSearch(value) { filterDraft = value; },
    query() { filterApplied = filterDraft; },
    resetSearch() { filterDraft = ''; filterApplied = ''; },
    list() { return copy(records.filter(row => row.name.includes(filterApplied))); },
    openDetail(id) {
      const row = records.find(row => row.id === id);
      if (!row) throw new Error('MEMBER_NOT_FOUND');
      detail = copy(row); groupDraft = null;
    },
    detail() { return copy(detail); },
    setName(name) { requireDetail(); detail.name = name; },
    setRegion(region) { requireDetail(); detail.region = region; },
    resetDetail() {
      requireDetail(); detail = copy(records.find(row => row.id === detail.id)); groupDraft = null;
    },
    cancelDetail() { detail = null; groupDraft = null; },
    saveDetail() {
      requireDetail();
      if (!detail.name.trim()) throw new Error('NAME_REQUIRED');
      records[records.findIndex(row => row.id === detail.id)] = copy(detail);
      detail = null; groupDraft = null;
    },
    openGroups() { requireDetail(); groupDraft = [...detail.groups]; },
    availableGroups(search = '') {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      return copy(GROUPS.filter(g => !groupDraft.includes(g.id) && g.name.includes(search)));
    },
    selectedGroups(search = '') {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      return copy(groupDraft.map(id => GROUPS.find(g => g.id === id)).filter(g => g.name.includes(search)));
    },
    addGroup(id) {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      if (!GROUPS.some(g => g.id === id)) throw new Error('GROUP_NOT_FOUND');
      if (!groupDraft.includes(id)) groupDraft.push(id);
    },
    removeGroup(id) {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      groupDraft = groupDraft.filter(item => item !== id);
    },
    moveGroup(from, to) {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      if (![from, to].every(n => Number.isInteger(n) && n >= 0 && n < groupDraft.length)) throw new Error('INVALID_ORDER');
      const [item] = groupDraft.splice(from, 1); groupDraft.splice(to, 0, item);
    },
    cancelGroups() { groupDraft = null; },
    confirmGroups() {
      if (!groupDraft) throw new Error('GROUP_PICKER_CLOSED');
      detail.groups = [...groupDraft]; groupDraft = null;
    },
  };
}
