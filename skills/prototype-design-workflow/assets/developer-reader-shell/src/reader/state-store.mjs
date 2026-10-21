export const initialState = Object.freeze({
  focusCardId: null,
  expandedCardIds: new Set(),
  expandedSourceIds: new Set(),
  panelCollapsed: false,
  navigationError: null,
  navigationPendingCardId: null,
  returnPoint: null
});

export function reduce(state, event) {
  switch (event.type) {
    case 'FOCUS_CARD': {
      const expandedCardIds = new Set(state.expandedCardIds);
      expandedCardIds.add(event.cardId);
      return {
        ...state,
        focusCardId: event.cardId,
        expandedCardIds,
        navigationError: null,
        navigationPendingCardId: null
      };
    }
    case 'CLEAR_FOCUS':
      return {...state, focusCardId: null};
    case 'TOGGLE_CARD': {
      const expandedCardIds = new Set(state.expandedCardIds);
      if (expandedCardIds.has(event.cardId)) expandedCardIds.delete(event.cardId);
      else expandedCardIds.add(event.cardId);
      return {...state, expandedCardIds};
    }
    case 'TOGGLE_SOURCE': {
      const expandedSourceIds = new Set(state.expandedSourceIds);
      if (expandedSourceIds.has(event.sourceId)) expandedSourceIds.delete(event.sourceId);
      else expandedSourceIds.add(event.sourceId);
      return {...state, expandedSourceIds};
    }
    case 'TOGGLE_PANEL':
      return {...state, panelCollapsed: !state.panelCollapsed};
    case 'SET_RETURN_POINT':
      return state.returnPoint === null ? {...state, returnPoint: event.returnPoint} : state;
    case 'CLEAR_RETURN_POINT':
      return {...state, returnPoint: null};
    case 'NAVIGATION_ERROR':
      return {...state, navigationError: event.message};
    case 'CLEAR_NAVIGATION_ERROR':
      return {...state, navigationError: null};
    case 'START_PENDING_NAVIGATION':
      return state.navigationPendingCardId === null
        ? {...state, navigationPendingCardId: event.cardId}
        : state;
    case 'CLEAR_PENDING_NAVIGATION':
      return {...state, navigationPendingCardId: null};
    default:
      return state;
  }
}
