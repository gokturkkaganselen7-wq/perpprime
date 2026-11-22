import json

from cfos.mcp_server import TOOLS, handle


def _call(settings, name, arguments=None):
    response = handle(
        settings,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    return response


def _payload(response):
    content = response["result"]["content"][0]
    return json.loads(content["text"])


def test_initialize_returns_server_info(settings):
    response = handle(
        settings,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    result = response["result"]
    assert result["serverInfo"]["name"] == "cfos"
    assert "tools" in result["capabilities"]
    assert "kill_switch" in result["instructions"]


def test_ping(settings):
    response = handle(settings, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert response["result"] == {}


def test_tools_list_names(settings):
    response = handle(settings, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    expected = {
        "cfos_status",
        "cfos_doctor",
        "cfos_market",
        "cfos_regime",
        "cfos_allocate",
        "cfos_route",
        "cfos_size",
        "cfos_plan",
        "cfos_execute",
        "cfos_positions",
        "cfos_close",
        "cfos_tick",
        "cfos_kill",
        "cfos_unkill",
        "cfos_journal",
        "cfos_review",
    }
    assert names == expected
    assert len(TOOLS) == 16


def test_status_tool(settings):
    response = _call(settings, "cfos_status")
    payload = _payload(response)
    assert payload["mode"] == "paper"
    assert payload["kill_switch"]["engaged"] is False
    assert "equity" in payload["account"]


def test_route_tool(settings):
    response = _call(settings, "cfos_route", {"intent": "trend"})
    payload = _payload(response)
    assert payload["intent"] == "trend"
    assert "cfos-sizing" in payload["skills"]


def test_size_tool(settings):
    response = _call(
        settings,
        "cfos_size",
        {"symbol": "BTC/USDT:USDT", "side": "long", "entry": 65000, "stop": 64000},
    )
    payload = _payload(response)
    assert payload["ok"] is True
    assert payload["qty"] == 0.05
    assert payload["risk_pct"] == 0.5


def test_plan_tool_rejects_bad_geometry(settings):
    response = _call(
        settings,
        "cfos_plan",
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "entry": 65000,
            "stop": 66000,
            "take_profits": [68000],
        },
    )
    # A tool error is returned as a tool result with isError=true.
    assert response["result"]["isError"] is True


def test_kill_then_unkill(settings):
    engaged = _payload(_call(settings, "cfos_kill", {"reason": "test"}))
    assert engaged["kill_switch"] == "ENGAGED"
    assert settings.kill_file.is_file()

    released = _payload(_call(settings, "cfos_unkill"))
    assert released["kill_switch"] == "released"
    assert not settings.kill_file.exists()


def test_unknown_tool_returns_jsonrpc_error(settings):
    response = _call(settings, "cfos_nuke")
    assert response["error"]["code"] == -32601


def test_notification_is_ignored(settings):
    response = handle(
        settings,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response is None


def test_unknown_method(settings):
    response = handle(settings, {"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert response["error"]["code"] == -32601
