import copy
import time
import common
import larkm as lark
from parse_result import ParseResult, RemainderState
from lark.lexer import Token
from larkm import Lark
from typing import Optional, Any, Tuple


class IncrementalParser:    
    """
    This is the base class for all incremental parsers.
    """
    def __init__(self, grammar_file, logger: Optional[common.Logger]=None, indenter=None, parser="lalr") -> None:
        self.cur_ac_terminals: Optional[set] = None
        self.next_ac_terminals: Optional[set] = None
        self.cur_pos = 0 # Current cursor position in the lexer tokens list
        self.lexer_pos = 0 # Current lexer position in the code
        self.dedent_queue: list = []

        # Initialize the parser
        time_start = time.time()
        self.parser = Lark.open( # This is the standard Lark parser
            grammar_file,
            parser=parser,
            lexer="basic",
            start="start",
            postlex=indenter,
            propagate_positions=True,
        )

        self.logger = logger if logger is not None else common.TestLogger()
        self.logger.log_time(f"Time taken for loading parser: {time.time() - time_start:.2f}s")

        self.interactive = self.parser.parse_interactive('')
        self.parser_token_seq: list = []

        # To enable going back to old state of the parser
        self.prev_lexer_tokens: list[Token] = []
        
        # parser_state, cur_ac_terminals, next_ac_terminals, indent_levels (optional), dedent_queue
        self.cur_pos_to_parser_state: dict[int, Tuple[Any, Optional[set], Optional[set], Optional[list], list]] = {}

        # Profiling
        self.time_accepts = 0
    
    def _store_parser_state(self, pos: int, parser_state, accepts: Optional[set], indent_levels: Optional[list] = None):  
        time_start = time.time() 
        cur_ac_terminals = self.next_ac_terminals  
        next_ac_terminals = accepts 
        
        # parser_state, cur_ac_terminals, next_ac_terminals, indent_levels, dedent_queue
        self.cur_pos_to_parser_state[pos] = (parser_state, cur_ac_terminals, next_ac_terminals, indent_levels, copy.deepcopy(self.dedent_queue))
        
        self.cur_ac_terminals = copy.deepcopy(cur_ac_terminals)
        self.next_ac_terminals = copy.deepcopy(next_ac_terminals)
        self.logger.log_time(f'Time taken for storing parser state:{time.time() - time_start}')

    def _restore_parser_state(self, pos: int):
        time_start = time.time()
        # parser_state, cur_ac_terminals, next_ac_terminals, indent_levels, dedent_queue
        parser_state, cur_ac_terminals, next_ac_terminals, indent_levels, dedent_queue = self.cur_pos_to_parser_state[pos]
        self.interactive.parser_state = parser_state.copy()

        self.dedent_queue = copy.deepcopy(dedent_queue)
        self.cur_ac_terminals = copy.deepcopy(cur_ac_terminals)
        self.next_ac_terminals = copy.deepcopy(next_ac_terminals)

        if indent_levels is not None:
            self.indent_level = copy.deepcopy(indent_levels)

        self.logger.log_time(f'Time taken for restoring parser state:{time.time() - time_start}')

    def _lex_code(self, code) -> list[Token]:
        """
        Lexes the given code and returns the list of tokens.
        """
        # Collect Lexer tokens
        lexer_tokens: list[Token] = []
        interactive = self.parser.parse_interactive(code)
        lexing_start_time = time.time()
        lexer_state = interactive.lexer_thread.state

        try:
            while lexer_state.line_ctr.char_pos < len(lexer_state.text):
                blexer = interactive.lexer_thread.lexer
                token = blexer.next_token(lexer_state)
                self.lexer_pos = lexer_state.line_ctr.char_pos
                lexer_tokens.append(token)
        except lark.exceptions.UnexpectedCharacters as e:
            pass
        except EOFError as e:
            pass
        self.lexer_pos = lexer_state.line_ctr.char_pos
        self.logger.log_time(f'Time taken for lexing:{time.time() - lexing_start_time}')
        return lexer_tokens
    
    def _restore_recent_parser_state(self, lexer_tokens):
        """
        Restores the parser state to the most recent prefix matching state that was stored. 
        """
        max_matching_index = -1
        for i in range(min(len(self.prev_lexer_tokens), len(lexer_tokens))):
            if self.prev_lexer_tokens[i] != lexer_tokens[i]:
                break
            if i in self.cur_pos_to_parser_state:
                max_matching_index = i

        if max_matching_index != -1:
            self.cur_pos = max_matching_index + 1
            assert (max_matching_index) in self.cur_pos_to_parser_state
            self._restore_parser_state(max_matching_index)


    def get_acceptable_next_terminals(self, partial_code) -> ParseResult:
        """
        Returns the set of acceptable terminals at the current partial code position.
        """
        # Stores the sequence of tokens that the parser has seen in the order  
        interactive = self.interactive
        lexer_tokens: list[Token] = self._lex_code(partial_code)

        # Restore the previous state of the parser
        if self.prev_lexer_tokens is not None:
            self._restore_recent_parser_state(lexer_tokens)
        
        self.prev_lexer_tokens, next_ac_indents = lexer_tokens, None  # Set the previous lexer tokens

        # Parse the tokens
        parsing_start_time = time.time()
        self.time_accepts = 0
        
        try:
            while self.cur_pos < len(lexer_tokens):
                token = lexer_tokens[self.cur_pos]
                self.cur_pos += 1
                self.parser_token_seq.append(token) # parser_token_seq holds all tokens
                interactive.feed_token(token)

                # Store the current state of the parser
                self._store_parser_state(
                    self.cur_pos-1, 
                    interactive.parser_state.copy(), 
                    self._accepts(interactive))

        except lark.exceptions.UnexpectedToken as e:
            pass

        self.logger.log_time(f'Time taken for parsing:{time.time() - parsing_start_time}')
        self.logger.log_time(f'Time taken for computing accepts:{self.time_accepts}')

        # Compute current terminal string
        remainder_state, current_term_str = self._get_remainder(partial_code)
        
        return ParseResult(self.cur_ac_terminals, self.next_ac_terminals, current_term_str, remainder_state, next_ac_indents=next_ac_indents)

    def _get_remainder(self, code):
        if self.lexer_pos < len(code):
            remainder_state = RemainderState.INCOMPLETE
            current_term_str = code[self.lexer_pos:]
            current_term_str = current_term_str.lstrip(' ') # Remove space from the beginning
            if current_term_str == '':
                remainder_state = RemainderState.COMPLETE
        else:
            # Although this is a complete terminal, it may happen that this may be just prefix of some other terminal
            # e.g., 'de' may seem like a variable name that is complete, but it may be just a prefix of 'def'
            current_term_str = self.parser_token_seq[-1].value
            remainder_state = RemainderState.MAYBE_COMPLETE
        return remainder_state,current_term_str
    
    def _accepts(self, interactive_parser):
        start_time = time.time()
        accepts = interactive_parser.accepts()
        self.time_accepts += time.time() - start_time
        return accepts
        