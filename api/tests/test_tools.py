import pytest

from app.services.grading.tools import verify_calculation


@pytest.mark.parametrize("expr,holds", [
    ("2*4 + 3 = 11", True),
    ("2*4 + 3 = 12", False),
    ("diff(x^3, x) = 3*x^2", True),
    ("integrate(2*x, x) = x^2", True),
])
async def test_equations(expr, holds):
    assert (await verify_calculation(expr))["holds"] is holds


async def test_rounded_student_values_are_approximately_equal():
    r = await verify_calculation("sqrt(2) = 1.414")
    assert r["holds"] is False and r["approximately_equal"] is True


async def test_expression_value():
    assert (await verify_calculation("pi*2^2"))["numeric"].startswith("12.566")


@pytest.mark.parametrize("malicious", [
    '__import__("os").system("ls")',
    "x.subs(x, 1)",
    "open(1)",
    "eval(1)",
    "9**9**9",
    "10^100000",
    "factorial(100000)",
    "(10^1000)^1000",
    "1" * 50,
    "a" * 400,
])
async def test_rejects_unsafe_input(malicious):
    r = await verify_calculation(malicious)
    assert "error" in r
