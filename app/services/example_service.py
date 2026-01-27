"""
Example service to demonstrate the service layer pattern.
Services contain business logic separate from routes and models.
"""


class ExampleService:
    """
    Example service class.

    Services typically:
    - Contain business logic
    - Interact with models to read/write data
    - Call external APIs
    - Process and transform data
    """

    @staticmethod
    def process_data(data):
        """
        Example method that processes some data.

        Args:
            data: Input data to process

        Returns:
            Processed data
        """
        # Add your business logic here
        return {'processed': True, 'data': data}

    @staticmethod
    def validate_input(input_data):
        """
        Example validation method.

        Args:
            input_data: Data to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not input_data:
            return False, "Input cannot be empty"
        return True, None
