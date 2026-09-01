export function themeScript(guidedSignedIn: boolean) {
  return `
(function(){
  var path = location.pathname === '/' ? '/' : location.pathname.replace(/\\/+$/, '');
  var demoRoute = path === '/' || path === '/recovered' || path.startsWith('/recovered/') || path === '/promises' || path.startsWith('/promises/') || path === '/audit' || path.startsWith('/audit/') || path === '/invoices' || path.startsWith('/invoices/') || path === '/settings' || path.startsWith('/settings/');
  var liveRoute = path === '/live' || (path.startsWith('/live/') && path !== '/live/login');
  var dashboardRoute = liveRoute || (${guidedSignedIn} && demoRoute);
  if (!dashboardRoute) {
    document.documentElement.setAttribute('data-theme', 'dark');
    return;
  }
  try {
    var saved = localStorage.getItem('vasooli-theme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
      return;
    }
    var prefersDark = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`;
}
