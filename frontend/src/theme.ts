import { createTheme } from '@mui/material';
import { zhCN } from '@mui/material/locale';

// 与 docs/inkflow-preview.html 的默认浅色样板共用颜色角色。
// CSS、MUI 与流程图都从这里取色，建议类型和运行状态分别表达含义。
export const colors = {
  page: '#ededf1', sidebar: '#f7f7fa', workspace: '#f4f4f7', panel: '#fcfcfd', paper: '#ffffff',
  ink: '#1b1b23', body: '#41414a', muted: '#676774', line: '#e7e7ee', soft: '#efeff4',
  focus: '#69529e', expression: '#276e53', 'expression-bg': '#eaf6ef', 'expression-border': '#cee4d7',
  structure: '#69529e', 'structure-bg': '#f1edfa', 'structure-border': '#ded5f0',
  consistency: '#3e6596', 'consistency-bg': '#edf3fc', 'consistency-border': '#d5e1f2',
  warning: '#935b16', 'warning-bg': '#fbf3e6', 'warning-border': '#ead8b8',
  danger: '#b13f3f', 'danger-bg': '#fbeeee', 'danger-border': '#ebcdcd',
};

export const theme = createTheme({
  palette: {
    primary: { main: colors.ink }, background: { default: colors.page, paper: colors.paper },
    text: { primary: colors.ink, secondary: colors.muted }, divider: colors.line,
    success: { main: colors.expression }, info: { main: colors.consistency },
    warning: { main: colors.warning }, error: { main: colors.danger },
    action: { hover: colors.soft, selected: colors.soft },
  },
  typography: {
    fontFamily: '"Segoe UI", "Microsoft YaHei", "Noto Sans SC", sans-serif', fontSize: 13,
    button: { textTransform: 'none', fontWeight: 500 }, h5: { fontWeight: 650 }, h6: { fontWeight: 600 },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiCssBaseline: { styleOverrides: { ':root': Object.fromEntries(Object.entries(colors).map(([key, value]) => [`--${key}`, value])) } },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { minHeight: 36, whiteSpace: 'nowrap', '@media (pointer: coarse)': { minHeight: 44 } },
        outlined: { borderColor: colors.line, backgroundColor: colors.paper },
      },
    },
    MuiIconButton: { styleOverrides: { root: { color: colors.muted, borderRadius: 8, '@media (pointer: coarse)': { minWidth: 44, minHeight: 44 } } } },
    MuiTextField: { defaultProps: { size: 'small', fullWidth: true } },
    MuiOutlinedInput: { styleOverrides: { root: { backgroundColor: colors.paper }, notchedOutline: { borderColor: colors.line } } },
    MuiPaper: { defaultProps: { elevation: 0 } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 16, border: `1px solid ${colors.line}`, boxShadow: '0 16px 64px #25253d20' } } },
    MuiDialogTitle: { styleOverrides: { root: { fontSize: 17, padding: '20px 24px' } } },
    MuiDialogActions: { styleOverrides: { root: { padding: '16px 24px', borderTop: `1px solid ${colors.line}`, flexWrap: 'wrap', gap: 8 } } },
    MuiChip: { styleOverrides: { root: { borderRadius: 6, fontSize: 11, height: 26 } } },
    MuiTooltip: { defaultProps: { arrow: true } },
  },
}, zhCN);
