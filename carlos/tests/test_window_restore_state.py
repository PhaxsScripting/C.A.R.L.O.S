import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from ev.tools.builtin import desktop_set_window_state


class WindowRestoreStateTests(unittest.IsolatedAsyncioTestCase):
    async def result(self, mode, maximized=False):
        observed={'id':'owned','minimized':False,'fullscreen':False,'maximized':maximized}
        if mode != 'missing':observed['maximize_mode']=mode
        model=SimpleNamespace(bridge=SimpleNamespace(request=AsyncMock()),
            snapshot=AsyncMock(return_value={'windows':[observed]}),
            visible_windows=lambda world:world['windows'],remember_window=Mock())
        with patch('ev.tools.builtin._window_after',AsyncMock(return_value=observed)):
            return await desktop_set_window_state({'window_id':'owned','state':'restore'},SimpleNamespace(desktop=model))

    async def test_native_restored_state_overrides_area_sized_geometry_flag(self):
        self.assertTrue((await self.result(0, maximized=True))['verified'])

    async def test_partial_full_and_invalid_states_are_not_restored(self):
        for mode in (1,2,3,False,'0',4):
            self.assertFalse((await self.result(mode))['verified'],mode)

    async def test_older_backend_falls_back_to_maximized_flag(self):
        self.assertTrue((await self.result('missing'))['verified'])
        self.assertFalse((await self.result('missing',maximized=True))['verified'])
