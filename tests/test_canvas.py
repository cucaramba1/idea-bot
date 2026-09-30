import json

from ideabot.vault.canvas import (IDEAS_GROUP, Canvas, add_to_group, drop_empty_side_group,
                                  ensure_side_group, ensure_theme_group)

THEMES = ["Бизнес-идеи / заработок", "Контент", "Мысли и заметки", "Геймдизайн", "Музыка", "Разное"]


def test_load_save_preserves_foreign_fields(vault):
    p = vault / "Медиабаинг.canvas"
    cv = Canvas.load(p)
    add_to_group(cv, ensure_side_group(cv), "Творческие идеи/X.md")
    cv.save()
    data = json.loads(p.read_text("utf-8"))
    text_node = next(n for n in data["nodes"] if n["id"] == "t-1")
    assert text_node == {"id": "t-1", "type": "text", "x": 600, "y": 200, "width": 300, "height": 80, "text": "руками"}
    assert '"руками"' in p.read_text("utf-8")  # ensure_ascii=False


def test_missing_canvas_is_empty(tmp_path):
    cv = Canvas.load(tmp_path / "nope.canvas")
    assert cv.nodes == [] and cv.edges == [] and cv.bounds() is None


def test_theme_row_below_everything_and_reused_group_moves_with_children(vault):
    cv = Canvas.load(vault / "Творческие идеи.canvas")
    g = ensure_theme_group(cv, "Контент", THEMES)
    # ниже всего нетематического (группа «Разное — цитаты» кончается на 550) + 120
    assert (g["x"], g["y"]) == (500, 670)
    gd = ensure_theme_group(cv, "Геймдизайн", THEMES)
    assert gd["id"] == "g-gd" and (gd["x"], gd["y"]) == (1500, 670)
    child = cv.node("n-gd")
    assert (child["x"], child["y"]) == (1530, 720)
    misc = cv.node("g-misc")
    assert (misc["x"], misc["y"], misc["color"]) == (0, 320, "4")  # чужое не тронуто
    # повторный вызов ничего не двигает
    assert ensure_theme_group(cv, "Геймдизайн", THEMES)["y"] == 670


def test_add_to_group_stacks_and_grows(vault):
    cv = Canvas.load(vault / "Творческие идеи.canvas")
    g = ensure_theme_group(cv, "Контент", THEMES)
    a = add_to_group(cv, g, "Творческие идеи/A.md")
    b = add_to_group(cv, g, "Творческие идеи/B.md")
    assert a["y"] == g["y"] + 50 and b["y"] == a["y"] + 120 + 20
    assert g["y"] + g["height"] >= b["y"] + b["height"]
    assert {n["id"] for n in cv.children(g)} == {a["id"], b["id"]}


def test_side_group_right_of_content_and_cleanup(vault):
    cv = Canvas.load(vault / "Медиабаинг.canvas")
    g = ensure_side_group(cv)
    assert g["label"] == IDEAS_GROUP and g["x"] == 1000 + 200 and g["y"] == 0
    n = add_to_group(cv, g, "Творческие идеи/A.md")
    e = cv.add_edge(n["id"], "n-off")
    assert cv.file_node("Творческие идеи/A.md") is n
    cv.remove_node(n["id"])
    assert e not in cv.edges
    drop_empty_side_group(cv)
    assert cv.group(IDEAS_GROUP) is None
