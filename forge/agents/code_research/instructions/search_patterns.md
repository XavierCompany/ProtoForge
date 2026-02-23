# Code Search Patterns

## Search Strategies

| Strategy | When to Use | Approach |
|----------|-------------|----------|
| Exact match | Known function/class name | Grep for identifier |
| Regex pattern | Structural patterns | `r"def \w+_handler\("` |
| Semantic | Conceptual queries | Embedding-based search |
| Call graph | "Who calls X?" | AST analysis |
| Dependency | "What does X depend on?" | Import/require analysis |

## Language-Specific Patterns

### Python
- Functions: `def function_name(`
- Classes: `class ClassName(`
- Imports: `from module import` / `import module`
- Decorators: `@decorator_name`

### JavaScript/TypeScript
- Functions: `function name(` / `const name = (`
- Classes: `class ClassName`
- Imports: `import { } from` / `require(`
- Exports: `export default` / `export const`

### Java
- Methods: `public ReturnType methodName(`
- Classes: `public class ClassName`
- Imports: `import package.Class`

## Result Ranking

Rank results by relevance:
1. Exact name matches
2. Definition sites (where declared)
3. Primary usage sites (where called most)
4. Test files (lower priority unless specifically asked)
