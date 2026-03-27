#!/usr/bin/env bash
# ============================================================================
# evaluate.sh — 评估者 (Evaluator)
#
# 独立于执行者运行，对代码质量、测试覆盖、架构合规性做全面评估。
# 返回 0 表示通过，非 0 表示存在问题。
#
# 用法:
#   ./scripts/evaluate.sh          # 完整评估
#   ./scripts/evaluate.sh quick    # 快速评估（仅 lint + 单元测试）
#   ./scripts/evaluate.sh lint     # 仅 lint
#   ./scripts/evaluate.sh test     # 仅测试
#   ./scripts/evaluate.sh arch     # 仅架构检查
# ============================================================================

set -uo pipefail
# 注意: 不用 set -e，因为各检查项的失败由我们自己捕获和计数

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

# 自动激活 venv (如果存在)
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0
WARNINGS=0
REPORT=""

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
section() {
    echo -e "\n${BLUE}━━━ $1 ━━━${NC}"
    REPORT+="\n## $1\n"
}

pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    REPORT+="- ✓ $1\n"
    ((PASSED++))
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
    REPORT+="- ✗ $1\n"
    ((FAILED++))
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
    REPORT+="- ⚠ $1\n"
    ((WARNINGS++))
}

# ---------------------------------------------------------------------------
# 1. Lint 检查 (ruff)
# ---------------------------------------------------------------------------
run_lint() {
    section "代码质量检查 (ruff lint)"

    if ! command -v ruff &>/dev/null; then
        warn "ruff 未安装，跳过 lint 检查"
        return
    fi

    # Lint
    if ruff check server/ tests/ scripts/ --quiet 2>/dev/null; then
        pass "ruff lint: 无问题"
    else
        LINT_OUT=$(ruff check server/ tests/ scripts/ 2>/dev/null || true)
        LINT_COUNT=$(echo "$LINT_OUT" | grep -c "^" || echo "0")
        fail "ruff lint: 发现 ${LINT_COUNT} 个问题"
        echo "$LINT_OUT" | head -20
        if [ "$LINT_COUNT" -gt 20 ]; then
            echo "  ... (截断，共 ${LINT_COUNT} 个问题)"
        fi
    fi

    # Format check
    if ruff format --check server/ tests/ scripts/ --quiet 2>/dev/null; then
        pass "ruff format: 格式规范"
    else
        fail "ruff format: 格式不规范 (运行 ruff format 修复)"
    fi
}

# ---------------------------------------------------------------------------
# 2. 单元测试
# ---------------------------------------------------------------------------
run_tests() {
    section "单元测试 (pytest)"

    if ! command -v pytest &>/dev/null && ! python3 -m pytest --version &>/dev/null 2>&1; then
        warn "pytest 未安装，跳过测试"
        return
    fi

    # 排除需要 GPU/集成环境的测试
    if python3 -m pytest tests/ -v --tb=short -m "not slow and not gpu and not integration" --timeout=60 2>&1; then
        pass "单元测试全部通过"
    else
        fail "单元测试有失败"
    fi
}

# ---------------------------------------------------------------------------
# 3. 架构合规检查
# ---------------------------------------------------------------------------
run_arch_check() {
    section "架构合规检查"

    # 3.1 层级依赖方向: 上层可以导入下层，反之不行
    #   output → personality → memory → perception (允许)
    #   perception → memory (禁止)
    if grep -rn "from server\.memory" server/perception/ 2>/dev/null | grep -v "__pycache__"; then
        fail "感知层 (perception) 不应导入记忆层 (memory)"
    else
        pass "层级依赖方向正确: 感知层无记忆层导入"
    fi

    if grep -rn "from server\.personality" server/perception/ 2>/dev/null | grep -v "__pycache__"; then
        fail "感知层 (perception) 不应导入人格层 (personality)"
    else
        pass "层级依赖方向正确: 感知层无人格层导入"
    fi

    if grep -rn "from server\.personality" server/memory/ 2>/dev/null | grep -v "__pycache__"; then
        fail "记忆层 (memory) 不应导入人格层 (personality)"
    else
        pass "层级依赖方向正确: 记忆层无人格层导入"
    fi

    # 3.2 关键模块存在性
    REQUIRED_MODULES=(
        "server/perception/vad.py"
        "server/perception/speaker_id.py"
        "server/perception/asr.py"
        "server/memory/working_memory.py"
        "server/memory/episodic_memory.py"
        "server/memory/semantic_memory.py"
        "server/memory/long_term_profile.py"
        "server/memory/consolidation.py"
        "server/personality/engine.py"
        "server/personality/llm_client.py"
        "server/personality/prompt_builder.py"
        "server/personality/intervention.py"
    )
    MISSING=0
    for mod in "${REQUIRED_MODULES[@]}"; do
        if [ ! -f "$mod" ]; then
            fail "缺少关键模块: $mod"
            ((MISSING++))
        fi
    done
    if [ "$MISSING" -eq 0 ]; then
        pass "所有关键模块存在 (${#REQUIRED_MODULES[@]} 个)"
    fi

    # 3.3 每个模块有对应的测试文件 (模糊匹配: test_memory.py 覆盖 memory/ 下所有模块)
    TEST_COVERAGE=0
    TOTAL_LAYERS=0
    for layer in perception memory personality; do
        for py in server/$layer/*.py; do
            [ -f "$py" ] || continue
            basename=$(basename "$py" .py)
            [[ "$basename" == "__init__" || "$basename" == "__pycache__" ]] && continue
            ((TOTAL_LAYERS++))
            # 匹配: test_<basename>.py 或 test_<layer>.py 或 test_<layer>_<basename>.py
            # 或测试文件内容包含该模块的 import
            if ls tests/test_${basename}.py tests/test_${layer}.py tests/test_${layer}_${basename}.py 2>/dev/null | head -1 >/dev/null 2>&1; then
                ((TEST_COVERAGE++))
            elif grep -rl "from server.${layer}.${basename}" tests/ 2>/dev/null | head -1 >/dev/null 2>&1; then
                ((TEST_COVERAGE++))
            fi
        done
    done
    if [ "$TOTAL_LAYERS" -gt 0 ]; then
        PCT=$((TEST_COVERAGE * 100 / TOTAL_LAYERS))
        if [ "$PCT" -ge 60 ]; then
            pass "测试覆盖: ${TEST_COVERAGE}/${TOTAL_LAYERS} 模块有测试 (${PCT}%)"
        else
            warn "测试覆盖不足: ${TEST_COVERAGE}/${TOTAL_LAYERS} 模块有测试 (${PCT}%)"
        fi
    fi

    # 3.4 安全检查: 不应有硬编码的密钥/密码
    if grep -rn "api_key\s*=\s*\"[a-zA-Z0-9]" server/ --include="*.py" 2>/dev/null | grep -v "local\|test\|dummy\|example\|__pycache__"; then
        fail "发现硬编码的 API key"
    else
        pass "无硬编码 API key"
    fi

    # 3.5 不应有 print() 调用 (应该用 logging)
    PRINT_COUNT=$(grep -rn "^\s*print(" server/ --include="*.py" 2>/dev/null | grep -v "__pycache__" | wc -l)
    PRINT_COUNT=$((PRINT_COUNT + 0))  # 去掉空白
    if [ "$PRINT_COUNT" -gt 0 ]; then
        warn "server/ 中有 ${PRINT_COUNT} 处 print() 调用，建议改用 logging"
    else
        pass "server/ 中无 print() 调用，全部使用 logging"
    fi
}

# ---------------------------------------------------------------------------
# 4. 运行时健康检查 (仅在服务运行时)
# ---------------------------------------------------------------------------
run_health_check() {
    section "运行时健康检查"

    if curl -s http://localhost:8765/health >/dev/null 2>&1; then
        HEALTH=$(curl -s http://localhost:8765/health)
        STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
        if [ "$STATUS" = "ok" ]; then
            pass "服务健康: status=ok"
        else
            warn "服务状态异常: $STATUS"
        fi

        # 检查各模块状态
        for module in vad speaker_id face_id asr memory personality llm; do
            MOD_STATUS=$(echo "$HEALTH" | python3 -c "
import sys, json
d = json.load(sys.stdin).get('modules', {})
print(d.get('$module', 'missing'))
" 2>/dev/null || echo "unknown")
            case "$MOD_STATUS" in
                "ok"|"loaded"|"local"|"cloud"|"local+cloud") pass "模块 $module: $MOD_STATUS" ;;
                "fallback") warn "模块 $module: $MOD_STATUS (降级模式)" ;;
                "missing") warn "模块 $module: 未报告状态" ;;
                *) warn "模块 $module: $MOD_STATUS" ;;
            esac
        done
    else
        warn "服务未运行 (localhost:8765)，跳过运行时检查"
    fi
}

# ---------------------------------------------------------------------------
# 5. 端到端真人模拟测试 (需要服务运行)
# ---------------------------------------------------------------------------
run_e2e_test() {
    section "端到端真人模拟测试"

    if ! curl -s http://localhost:8765/health >/dev/null 2>&1; then
        warn "服务未运行，跳过端到端测试"
        return
    fi

    if python3 -m pytest tests/test_e2e_human_sim.py -v --tb=short -m integration --timeout=120 2>&1; then
        pass "端到端真人模拟测试全部通过"
    else
        fail "端到端真人模拟测试有失败"
    fi
}

# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------
print_summary() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  评估报告汇总${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${GREEN}通过: ${PASSED}${NC}  ${RED}失败: ${FAILED}${NC}  ${YELLOW}警告: ${WARNINGS}${NC}"

    if [ "$FAILED" -gt 0 ]; then
        echo -e "\n  ${RED}结论: 评估未通过，需要修复 ${FAILED} 个问题${NC}"
        exit 1
    elif [ "$WARNINGS" -gt 3 ]; then
        echo -e "\n  ${YELLOW}结论: 评估通过（有 ${WARNINGS} 个警告需关注）${NC}"
        exit 0
    else
        echo -e "\n  ${GREEN}结论: 评估通过${NC}"
        exit 0
    fi
}

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
MODE="${1:-full}"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  CompanionBot 评估者 (Evaluator)${NC}"
echo -e "${BLUE}  模式: ${MODE}  时间: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

case "$MODE" in
    quick)
        run_lint
        run_tests
        ;;
    lint)
        run_lint
        ;;
    test)
        run_tests
        ;;
    arch)
        run_arch_check
        ;;
    health)
        run_health_check
        ;;
    e2e)
        run_e2e_test
        ;;
    full)
        run_lint
        run_tests
        run_arch_check
        run_health_check
        run_e2e_test
        ;;
    *)
        echo "用法: $0 [full|quick|lint|test|arch|health|e2e]"
        exit 1
        ;;
esac

print_summary
